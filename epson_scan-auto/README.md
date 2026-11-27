# EPSON Scan 3.9 自动扫描

用脚本自动控制**经典 EPSON Scan 3.9**（`C:\Windows\twain_32\escndv\escndv.exe`）完成胶片扫描全流程，把十几步手工操作打包成一次命令。三套流程方案用 JSON 保存、命令行切换，一次启动跑完。

> ⚠️ 这是 **EPSON Scan 3.9 桌面软件**的 GUI 自动化，不是 Epson Scan 2（后者有 CLI，是另一套东西）。

---

## 一句话说明

经典 EPSON Scan 没有官方 CLI，只能操作窗口。本工具用 pywinauto 的 win32 backend 按控件名称定位、全程后台消息级操作（**不抢鼠标**），支持排名/社媒/后期三套流程一键切换，异常退出时按三步铁律优雅关闭软件、绝不杀进程。

---

## 它能做什么 / 不能做什么

| ✅ 能 | ❌ 不能 |
|---|---|
| 自动完成机型确定→主窗口设置→配置→文件保存→预览→扫描→等落盘 | 控制 Epson Scan 2（请用它的 CLI） |
| 三套流程方案 JSON 保存、命令行切换、随时加新方案 | 在全屏独占游戏里端到端自动化（机型选择会把游戏切回桌面） |
| 批量连续扫 N 张、每张提示换片 | 帮你物理重启扫描仪（冷启动这步只能手动） |
| 自动新建方案指定的输出文件夹、设 TIFF 子选项 | 救回已 clipping / 毁掉的暗部（那是扫描端的事） |

---

## 安装

Windows only，Python 3：

```bash
pip install pywinauto
```

实测 Python 3.13 / pywinauto 0.6.9。依赖 pywin32（pywinauto 会带上），用到 `win32gui`/`win32con`/`win32process`。

---

## 使用前必读（硬件约束）

**扫描仪必须先物理断电重启，再第一次打开 EPSON Scan 才正常**（这台机器的驱动特性）。一旦正常，可以连续扫一批。反复启停软件会让驱动紊乱，弹出「无法与扫描仪连接」。

因此默认 **attach 模式**：脚本只连接你已经打开的正常窗口，不会自己启动/关闭软件。冷启动那一步请你手动完成：

1. 扫描仪断电几秒再上电。
2. 打开 EPSON Scan，机型选择点「确定」，进入专业模式主窗口。
3. 运行脚本。

---

## 安装初始化（只做一次）

装完 skill 先运行 `python run_flow.py --init`（或双击 bat 菜单 0）。它读本机各固定盘剩余空间，按 4800dpi 单张 JPG≈20MB / TIFF≈188MB 算出可存张数，问一次存哪个盘，结果写进 `config.json`：

```
=== 扫描图像存储磁盘（安装初始化，仅问这一次）===
4800dpi 单张估算：JPEG≈20MB / TIFF≈188MB（实际随画面内容浮动）
  盘符          剩余        总容量     可存 JPG     可存 TIFF
  C:        6.7G     114.5G       341张         36张
  D:       30.3G     531.2G      1549张        164张  ← 当前设置
  E:       18.1G     124.0G       928张         98张
请选择存储磁盘（输入盘符如 D，直接回车沿用 D:）:
已保存存储磁盘 D: -> config.json
```

**之后所有扫描流程直接读这个配置，不再询问。** 换盘就重跑 `--init`；单次临时改盘用 `--drive D`。流程据此创建 `X:\A_爱普生扫描\{test|jpg|tiff}\`。

## 快速使用

```bash
python run_flow.py --init                                # 装完先做一次：选存储磁盘
python run_flow.py --list                                # 列出三套方案
python run_flow.py --flow rank                           # 排名档扫 1 张（磁盘读 config.json）
python run_flow.py --flow jpg_social --batch 5           # 社媒档连扫 5 张
python run_flow.py --flow tiff_post --launch             # 后期档，脚本自己启动
python run_flow.py --flow rank --dry-run                 # 只设置不扫描（验证控件定位）
```

**快速模式（效率优化）**：首次用某方案需 `--full` 跑一次完整流程（会写 `scan_state.json` 快照）。之后同一方案、配置未变时**默认直接走快速模式**——跳过模式/四项/配置对话框/文件保存设置/USM/ICE，只做「预览→全选→扫描」，EPSON 自动序号不覆盖旧图：

```bash
python run_flow.py --flow jpg_social --drive D --launch          # 有快照→自动快速；无快照→自动完整
python run_flow.py --flow jpg_social --drive D --launch --full   # 强制完整（改了配置/需求时）
python run_flow.py --flow jpg_social --drive D --launch --fast   # 强制快速
```
> 配置指纹自动比对：改了 flows.json 的该方案或 `--folder`/`--drive`，快速模式自动失效、退回完整。`scan_state.json` 是运行时状态，不进 git。

输出目录 = `X:\A_爱普生扫描\<方案子目录>\<前缀><起始号码>.<格式>`，例如 `D:\A_爱普生扫描\test\img001.tif`（X 来自 `--init` 存下的配置）。

---

## 三套流程方案

| 方案 | 用途 | 参数 | 输出文件夹 / 前缀 |
|---|---|---|---|
| `rank` | 排名打分（历史榜单同口径） | 48bit TIFF / 无色彩校正 / 曝光关 / 不压缩 / 嵌ICC / 4800dpi / USM无 | `A_爱普生扫描\test` / `img` |
| `jpg_social` | 社媒分享（不入排名） | 48bit入口→JPEG质量1/标准编码/嵌ICC / 色彩控制+Gamma2.2 / 连续自动曝光 / USM低 / DIGITAL ICE速度 | `A_爱普生扫描\jpg` / `img` |
| `tiff_post` | 后期处理（达芬奇/NamiColor） | 与 rank 相同 48bit TIFF，靠前缀+文件夹区分 | `A_爱普生扫描\tiff` / `raw` |

`rank` 和 `tiff_post` 的扫描参数**完全相同**，按拍板只靠前缀+文件夹区分，避免两套配置漂移。`tiff_post` 的色彩/曝光口径暂用「无色彩校正+曝光关」（可直接喂 [`tiff-to-exr`](../tiff-to-exr) 转 scene-linear EXR），标注为待定，备选口径见 SKILL.md。

**改方案只改 `flows.json`，不用动代码**。加新方案就在 `方案` 下加一块，字段分组：

- `main` — 主窗口设置（文档类型/胶片类型/位深/分辨率/USM/ICE）
- `config_dialog` — 配置对话框彩色页（色彩模式/曝光/Gamma）
- `filesettings` — 文件保存设置（格式/文件夹名/前缀/`TIFF子选项` 或 `JPEG质量`）
  - 选 JPEG 后会自动点「选项」进 JPEG Plug-in 设置，确保：**压缩等级=`JPEG质量`（1=最高，UI 左高质量右低质量）/ 编码=标准（非逐行）/ 嵌入ICC=勾选**，再确定返回。
  - 选 TIFF 时按 `TIFF子选项` 设字节序/压缩/嵌ICC（同一「选项」入口）。
- `behavior` — 流程行为（等待秒数/连续张数/换片方式）

预览页选项和全选/单选不进方案：沿用软件已保存状态、扫描固定全选。

---

## 命令行参数（run_flow.py）

| 参数 | 说明 |
|---|---|
| `--flow` | 方案名：`rank` / `jpg_social` / `tiff_post` |
| `--list` | 列出所有方案 |
| `--init` | 安装初始化：问一次存储磁盘写进 config.json（只用一次） |
| `--drive` | 临时覆盖存储磁盘（如 D）；不传则读 `--init` 存下的配置 |
| `--folder` | 新建的文件夹名，覆盖方案里的值 |
| `--launch` | 脚本自己启动软件（默认 attach 已打开的） |
| `--dry-run` | 只设置不扫描，验证控件定位 |
| `--batch` | 连续扫描张数 |
| `--interval` | 批内间隔秒数 |
| `--foreground` | 前台鼠标模式（默认后台消息，不抢鼠标） |
| `--select-one` | 只选 1 张（测试用；正常流程固定全选） |
| `--select-index N` | `--select-one` 时选第几帧（默认 1） |
| `--full` | 强制完整流程并重设所有配置（改配置后必用） |
| `--fast` | 强制快速模式（预览→全选→扫描，跳过配置） |

`--drive` / `--folder` / `--batch` / `--interval` 可临时覆盖方案值，不改 JSON。

---

## Windows 双击入口

`一键扫描.bat` 是给用户双击的菜单入口，默认全部走后台模式（不带 `--foreground`）。首次使用请把 bat 里的 `PY=` 改成你本机 Python 解释器路径。bat 不再自己问盘符——选择菜单后由 Python 入口统一展示容量表并询问。日常命令行用 `run_flow.py` 更灵活。

---

## 安全与铁律（改动前必读）

1. **绝不杀进程**：异常退出只能点软件自己的「关闭(&C)」/「确定」，轮询等 `escndv.exe` 真正消失。强杀会让下次启动弹连接错误。
2. **三步优雅关闭**：子窗口点「取消」→ 主窗口点「关闭(&C)」→ 等进程退出。绝不 `WM_CLOSE` 强关子窗口（会连带杀主窗口）。
3. **不发跨进程指针消息**：`TCM_GETITEMW` 等带指针的 commctrl 消息会让软件闪退；只读控件用 pywinauto 自带方法。
4. **GUI 操作间隔 0.5 秒**，连续太快会丢。
5. **默认后台消息模式**，不抢鼠标；全屏游戏场景不可用。

详细踩坑记录见 [`SKILL.md`](SKILL.md) 的「关键铁律」章节。

---

## 文件结构

```
epson_scan-auto/
├── README.md                    本文件：给人看的作用与用法
├── SKILL.md                     给 agent 看的触发条件与工作流
├── epson_one_click.py           主脚本：全部 GUI 自动化 + run()
├── run_flow.py                  命令行入口：读 JSON → RunConfig → run()
├── flows.json                   方案库：rank / jpg_social / tiff_post
├── 一键扫描.bat                  Windows 双击菜单入口
├── requirements.txt             依赖清单
└── .gitignore                   排除日志/生成图/缓存
```

## 隐私

本工具只含方法学代码与文档，不含任何个人扫描图、EXR 或标注文件。发布前确认无个人信息、设备序列号或私人照片；生成的大体积 TIFF/JPG、日志、dump 留本地不入库。
