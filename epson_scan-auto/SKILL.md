---
name: epson_scan-auto
description: 用脚本自动控制经典 EPSON Scan 3.9（escndv.exe）完成胶片扫描全流程：机型选择→专业模式设置→配置对话框→文件保存设置（新建文件夹/格式/TIFF子选项）→预览→扫描→等待落盘。支持多套流程方案（rank 排名 / jpg_social 社媒 / tiff_post 后期）用 JSON 保存、命令行切换，全程后台消息级、不抢鼠标焦点、异常时优雅关闭不杀进程。当用户要自动扫描胶片、一键扫描、批量连续扫描、配置 EPSON Scan 自动流程、保存/切换扫描方案，或提到 epsondv/escndv 自动化时使用。触发词：一键扫描、自动扫描、批量扫描、EPSON Scan 自动化、扫描流程方案、rank/jpg_social/tiff_post。
agent_created: true
---

# EPSON Scan 3.9 自动扫描（epson_scan-auto）

## Overview

经典 EPSON Scan 3.9（`C:\Windows\twain_32\escndv\escndv.exe`）**没有官方 CLI**，本 skill 用 pywinauto 的 win32 backend 走 Win32 控件自动化，把截图《基础操作流程》的十几步手工操作打包成一次命令。核心是**一套脚本 + 一份 JSON 方案库**：三套流程按用途分，命令行 `--flow` 切换，一次启动跑完，不搞两次启停。

```
flows.json 方案库 → run_flow.py 组装 RunConfig → epson_one_click.run()
   → 机型确定 → 主窗口设置 → 配置对话框 → 文件保存设置 → 预览/选帧 → 扫描 → 等落盘
```

## When to use

- 自动扫描胶片、批量连续扫描 N 张、告别手工预览等待。
- 保存/切换多套扫描流程方案（排名档 / 社媒档 / 后期档）。
- 配置 EPSON Scan 专业模式各选项（48bit、分辨率、USM、色彩、曝光、格式、TIFF 子选项）。

**不适用**：Epson Scan 2（是另一套软件，有 CLI，本 skill 不管）；全屏独占游戏场景下无法端到端自动化（机型选择/浏览文件夹会把游戏切回桌面）。

## 依赖安装

```bash
pip install pywinauto
```

Windows only（依赖 pywin32 的 `win32gui`/`win32con`/`win32process`）。实测 Python 3.13 / pywinauto 0.6.9。脚本对中文路径安全（`np.fromfile` + `imdecode` 思路，控件按名称定位不依赖路径编码）。

## 安装初始化（只做一次，先于所有扫描）

`python run_flow.py --init`（或双击 `一键扫描.bat` 菜单 0）：读本机固定盘剩余空间，按 4800dpi 单张 JPG≈20MB / TIFF≈188MB 算出各盘可存张数，问一次用户存哪个盘，写入 `config.json` 的 `存储磁盘`。**之后所有扫描流程直接读该配置，不再询问**；换盘重跑 `--init`，单次临时改盘用 `--drive`。

## 快速开始

```bash
python run_flow.py --init                              # 装完先做一次：选存储磁盘
python run_flow.py --list                              # 列出三套方案
python run_flow.py --flow rank                         # attach 模式跑排名档（软件须已打开）
python run_flow.py --flow tiff_post --launch           # 自己启动跑后期档
python run_flow.py --flow jpg_social --batch 5         # 社媒档连扫 5 张
python run_flow.py --flow rank --dry-run               # 只设置不扫描（验证控件定位）
```

`--drive` 临时覆盖输出磁盘（否则读 `--init` 的配置）；`--folder` 覆盖方案里的文件夹名；`--launch` 自己启动软件（默认 attach 已打开的）；`--batch`/`--interval`/`--foreground`/`--select-one`/`--select-index` 可临时覆盖方案值。

**快速模式（默认开启）**：`scan_state.json` 记录每个方案最近一次完整配置的时间戳与配置指纹。首次用某方案（或改了配置）跑完整流程后会写快照；之后同一方案配置未变时默认走快速模式——跳过模式/四项/配置对话框/文件保存设置/USM/ICE，直接「预览→全选→扫描」，靠 EPSON 持久化的设置与自动序号（不覆盖旧图）。`--full` 强制完整、`--fast` 强制快速；配置指纹自动比对，改了方案或 `--folder`/`--drive` 会自动退回完整模式。`scan_state.json` 为运行时状态，不进 git。

## 三套流程方案（用途区分）

| 方案 | 用途 | 扫描参数 | 输出 |
|---|---|---|---|
| `rank` | 排名打分（历史榜单同口径） | 48bit TIFF / 无色彩校正 / 曝光关 / 不压缩 / 嵌ICC / 4800dpi / USM无 | `X:\A_爱普生扫描\test\img*.tif` |
| `jpg_social` | 社媒分享（不入排名） | 48bit→JPEG质量1/标准/嵌ICC / 色彩控制+Gamma2.2 / USM低 / ICE速度 | `X:\A_爱普生扫描\jpg\img*.jpg` |
| `tiff_post` | 后期处理（达芬奇/NamiColor） | 与 rank 相同 48bit TIFF，靠前缀 `raw`+独立文件夹区分 | `X:\A_爱普生扫描\tiff\raw*.tif` |

`rank` 与 `tiff_post` 扫描参数完全相同，**仅靠前缀+文件夹区分**（用户拍板），避免两套配置漂移。要改方案只改 `flows.json`，不动代码。加新方案=在 JSON `方案` 下加一块。

`tiff_post` 的色彩/曝光口径标注为「待定」：当前暂用无色彩校正+曝光关（可直接喂 `tiff-to-exr` 模块转 scene-linear EXR），备选①自动曝光压暗+后期提亮 ②色彩控制+Gamma1.8+连续自动曝光。切换只改 `tiff_post.config_dialog` 组。

JSON 字段分组：`main`（主窗口）/ `config_dialog`（配置对话框彩色页）/ `filesettings`（文件保存设置，含 `文件夹名`/`TIFF子选项`/`JPEG质量`）/ `behavior`（流程行为）。预览页选项与全选/单选**不进方案**（沿用软件已保存状态 / 固定全选）。

**JPEG 插件选项（2026-11-27）**：格式选 JPEG 后脚本会点「选项(&O)...」进「爱普生 JPEG Plug-in 设置」，消息级设三项并回读——压缩等级=`JPEG质量`（msctls_trackbar32 滑块，1=最高质量=最左，UI 范围 1-100；那个无文本 Edit 是自绘显示框，`GetWindowText`/`WM_SETTEXT` 恒为空，**不能当判据**）、编码=标准（`标准(&S)` radio，非逐行）、嵌入ICC 勾选，再点确定返回。实测控件名见工作目录 `jpeg_plugin_dump.txt`。

## 工作流（跑一次完整扫描）

1. **硬件准备（只能用户手动）**：扫描仪**物理断电重启**后第一次打开 EPSON Scan，机型选择点确定，停在专业模式主窗口。反复启停会让驱动紊乱弹「无法与扫描仪连接」——脚本默认不启停就是为了避开。
2. **attach 模式**（默认）：`python run_flow.py --flow <名>`（磁盘读 `--init` 的配置）。脚本只连接已打开的窗口，设置→扫描→等落盘。
   **launch 模式**（`--launch`）：脚本自己启动，退出时走三步优雅关闭。
3. **设置阶段**：主窗口选项 → 配置对话框（切色彩模式会弹确认框，脚本自动点「确定」）→ 文件保存设置（浏览对话框里新建方案指定文件夹并选中，回读路径框校验）。
4. **扫描阶段**：预览 → 全选 → 点扫描 → 等「进程」窗口结束 → 约 10 秒后落盘；脚本轮询新文件（size>0 才算完成）。
5. **收尾**：attach 模式不关软件；launch 模式三步优雅关闭（见铁律）。

## 关键铁律（踩过的坑，务必遵守）

- **扫描仪冷启动铁律**：物理断电重启后第一次打开才正常；脚本绝不反复启停软件。默认 attach，`--launch` 时才自己启动。
- **优雅关闭三步**：① 枚举并点「取消」关所有属于 escndv PID 的子窗口（配置/文件保存设置/浏览文件夹/预览）→ ② 点主窗口「关闭(&C)」（或错误框「确定」）→ ③ 轮询等 `escndv.exe` 进程真正消失。**绝不 `taskkill /F`、绝不 `WM_CLOSE` 强关子窗口**（强关模态子框会连带杀主窗口、驱动不复位）。
- **闪退根因**：跨进程发带指针的 commctrl 消息（`TCM_GETITEMW`/`TCM_GETITEMRECT` 等）会让 EPSON 进程访问违规退出。全项目只发整数消息（BM/CB/TBM/TVM_SELECTITEM/WM_SETTEXT），需要读控件用 pywinauto 自带方法（内部有 RemoteMemoryBlock 远程内存）。审计配置页用真实鼠标点标签切页，不用裸指针消息。
- **GUI 操作间隔 0.5 秒**：全局 `OP_DELAY`，连续操作太快会丢。
- **默认后台消息模式**：所有 `click_input`/`set_focus`/`bring_to_front` 都在 `if not BACKGROUND` 分支；bat 入口不带 `--foreground`。只在桌面环境用前台模式，全屏游戏场景下任何顶层窗口都会切回桌面。
- **0 字节残文件不算完成**：`wait_new_file()` 只认 size>0。
- **「进程」窗口可能延迟出现**：预热/无响应是正常现象，长超时、中性日志。

## 控件识别注意点

配置对话框 18 项控件已实测验证（18/18 识别成功）。三个匹配坑：实际页名是「彩色」不是「颜色」；ComboBox 文本是空的、靠上方 Static 标签定位；助记符 `&` 要归一。主脚本的 `apply_config_items()` 已处理这三点，正常流程无需额外操作。

## 配套文件

| 文件 | 作用 |
|---|---|
| `epson_one_click.py` | 主脚本：全部 GUI 自动化逻辑 + 主流程 `run()` |
| `run_flow.py` | 命令行入口：读 flows.json → 组装 RunConfig → run() |
| `flows.json` | 方案库：三套流程方案（rank/jpg_social/tiff_post） |
| `一键扫描.bat` | Windows 双击菜单入口 |
| `requirements.txt` | 依赖清单 |
| `SKILL.md` | 本文件：触发条件与工作流 |

## 隐私与发布

本模块只含方法学代码与文档，**不得打包任何用户自己的扫描图、EXR、标注文件**。发布前确认无个人用户名、主机名、私人目录路径、设备序列号、私人照片。生成的大体积 TIFF/JPG、日志、dump 文本留本地，不入库。
