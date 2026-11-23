# 倾斜标靶焦平面扫描 · 片夹垫高计算（tilt-focus-sweep）

利用 135 调焦卡（USAF 1951 黑白标靶）长条扫描图，计算胶片扫描仪片夹的最佳垫高高度，  
消除因片夹与玻璃不平行导致的离焦模糊。采用**两遍测试法**：第 1 遍给区间建议、第 2 遍给 0.1mm 精度的最终推荐垫高。

> 本项目以扫描对焦评估工作流沉淀而来，配套操作示意图见本目录（`tilt-focus-sweep/`）下的 `*.webp`。

## 目录结构

```
tilt-focus-sweep/
├── SKILL.md                            # WorkBuddy 技能元信息（name / description / agent_created）
├── README.md                           # 本说明
├── LICENSE                             # MIT
├── requirements.txt                    # opencv-python-headless / numpy / Pillow
├── .gitignore                          # 排除私有扫描数据（大图 / focus_history.json 等）
├── tilt_focus_sweep.py                 # 主脚本：倾斜长条焦平面扫描 + 垫高换算
├── focus_compare.py                    # 复用：细密精度锐度 + 分方向限量分辨率
├── usaf_resolve.py                     # 依赖：USAF 1951 极限分辨率测量（被 focus_compare 导入）
├── gen_rank_md.py                      # 可选：从 history 生成排名总榜
├── 计算垫高教程.md  # 方法详解（含推荐操作流程图文）
└── *.webp                             # 操作示意图 5 张（epson 扫描流程 / 垫片规格0.1·0.2mm / 调焦卡放置 / 垫片粘贴）
```

## 安装

### 1. Python 依赖

```bash
pip install -r requirements.txt
```

依赖：`opencv-python-headless`（或 `opencv-python`）、`numpy`、`Pillow`。  
所有脚本使用 `np.fromfile` + `cv2.imdecode` 读取图像，可正确处理中文 / Unicode 路径。

### 2. 作为 WorkBuddy skill 安装

本仓库的 skill 位于 `tilt-focus-sweep/` 子目录。安装时**复制该子目录**（而非整个仓库根）到以下任一位置，WorkBuddy 会自动读取其中的 `SKILL.md` 完成注册：

- 用户级（所有项目可用）：`~/.workbuddy/skills/tilt-focus-sweep/`
- 项目级（仅当前项目）：`{你的工作区}/.workbuddy/skills/tilt-focus-sweep/`

复制完成后无需额外命令，在对话中提到「片夹垫高 / 调焦卡 / tilt-focus-sweep」等关键词即可触发本 skill。  
文件夹内的 `tilt_focus_sweep.py` 也可脱离 WorkBuddy 单独用 `python` 调用。

## 使用

```bash
# 第 1 遍（粗，提交第 1 张 TIFF）：高低差 4mm，均分 40 份 → 图底部红字给第 2 遍区间建议 X–X+1mm
python tilt_focus_sweep.py <第1张长条图.tif> --lift-mm 4 --n 40

# 第 2 遍（精，提交第 2 张 TIFF）：按红字区间装夹（低端 X、高端 X+1），必须带 --low-lift-mm
python tilt_focus_sweep.py <第2张长条图.tif> --lift-mm 1.0 --n 10 --low-lift-mm <X>
```

输出：

- `tilt_sweep_profile.png`：双面板曲线（上=灰度锐度得分，下=R/G/B 三通道锐度曲线 + 各自峰值点（不标 LoCA））+ 窗口标记 + **底部红字横幅 = 本遍固定结果**（第1遍=第2遍区间建议；第2遍=最终推荐垫高 X.X–X.X+0.1mm）
- `tilt_best_window.jpg`：原图全宽回截最佳高度区间（红框圈最高份）
- 控制台打印：最佳份 Y、Zf、三通道峰值份/LoCA、以及本遍建议/最终推荐（与红字横幅一致）
- 色差口径 = 纵向色差 LoCA（三通道锐度峰值份差换算高度差），只诊断不计分；评分始终用灰度锐度
- **整个测试只需提交 2 张 TIFF**：第 1 张粗测定区间、第 2 张精测定最终值

## 核心公式

```
Zf = X × Y / n
```

- X：高低端垫高差(mm)；n：长条均分份数；Y：综合锐度最高份序号(1..n)
- 四个支脚统一垫起 `Zf` → 片夹整体平行于玻璃
- 第 1 遍第二遍区间建议（跨度 1mm）：**常规以 Zf 居中** = Zf−0.5 – Zf+0.5 mm（低端<0 钳到 0），峰值前后各覆盖 0.5mm；**唯一特例**——峰值恰贴第一遍最后一份（第 40 份，Zf=X）时，改为**从 Zf 起向上外探** = X – X+1 mm（Zf 已是实测最大值，真实最佳可能在更高处，居中会漏掉上方）
- 第二遍必须传 `--low-lift-mm`（低端绝对垫高，即使为 0 也要写 `--low-lift-mm 0`），脚本据此判定为第 2 遍并输出**最终推荐垫高 = ⌊绝对最佳/0.1⌋×0.1 – +0.1 mm**（四脚统一垫到此区间）

## 两遍精测法（推荐）

| 遍次        | 提交   | 高低差 X      | 份数 n | 输出（图底部红字固定）          | 分辨率       |
| --------- | ---- | ---------- | ---- | ------------------------ | --------- |
| 第 1 遍（粗）  | 第1张 TIFF | 4mm        | 40   | 第 2 遍区间建议（Zf 居中，贴第40份时向上外探；跨度 1mm） | 0.100mm/份 |
| 第 2 遍（精）  | 第2张 TIFF | 1mm（区间跨度） | 10   | 最终推荐垫高 X.X–X.X+0.1mm  | 0.100mm/份 |

第 1 遍把区间缩进到 1mm。**常规情形以峰值 Zf 居中**（Zf−0.5 – Zf+0.5，低端<0 钳到 0；例 Zf=3.9→3.4–4.4mm），峰值前后各覆盖 0.5mm。**唯一特例**：峰值恰贴第一遍最后一份（第 40 份，Zf=4.0）时改为**从 Zf 向上外探 4–5mm**——因为 Zf 已是第一遍实测最大值，真实最佳可能在更高处，居中会漏掉上方。第 2 遍按该区间装夹（低端 lo / 高端 lo+1），结果直接给出 0.1mm 精度的最终推荐，无需心算。想更细可 `--n 20`（0.05mm/份）。

## License

脚本以 MIT 许可证发布（如需变更请在本仓库补充说明）。操作示意图版权归原作者所有，  
转载使用前请取得授权。
