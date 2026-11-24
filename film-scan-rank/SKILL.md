---
name: film-scan-rank
description: 平板扫描仪（爱普生 V700/V800 等）多配置底片扫描的「同帧对焦/锐度质量排名」工具。8 向 Pearson 朝向枚举 + SIFT 几何校验（不 warp 原图、只在原图像素上算指标）+ 中心裁窗算 edge/gamut/span 三指标，以基线组=100 归一排名。含 JPG(0.70/0.15/0.15) 与 TIFF(0.85/0.15/0.00) 两条独立管线，各有全量与加测入口。适用于胶片扫描对焦评估、片夹/垫高工艺对比、扫描仪设置横评。需要 Python 3 + opencv-python / numpy / Pillow。
---

# film-scan-rank · 底片扫描对焦质量排名

把同一底片在不同扫描配置（片夹、垫高高度、是否 AR 玻璃、是否 M10 工艺等）下的扫描结果，
做**同帧配对 → 逐组平均 → 归一排名**，量化哪一组对焦最准、画质最好。

## 方法学核心（铁律）

- **对齐只算几何，指标在原图像素上算**：8 向枚举 + Pearson 相关定原生朝向（对暗/条纹/模糊内容比 SIFT 稳健）
  → SIFT+RANSAC 算单应 H 作几何质量校验 → H⁻¹ 把参考框投回原图得 AABB → **从原图直接裁出 AABB** → 中心取固定子区域。
  全程**不做 warpPerspective / resize 插值**，避免插值模糊系统性低估锐度。
- **三指标**（均以基线组归一，基线=100）：
  - `edge`（锐度）：中心裁窗内「梯度 ÷ 局部对比度」中位数，内容/曝光无关。
  - `gamut`（色域）：Lab 直方图占据格子数。
  - `span`（跨度）：亮度分布 p99.9−p0.1，动态范围代理。
- **两条管线，各自一套总分权重**：
  - **TIFF**（`rank_tiff.py`，读 `.tif/.tiff`）：总分 = **0.85×锐度 + 0.15×色域 + 0.00×跨度**；
    16bit 下跨度区分度低，不计入总分。
  - **JPG**（`rank_jpg.py`，读 `.jpg/.jpeg`）：总分 = **0.70×锐度 + 0.15×色域 + 0.15×跨度**；
    8bit 有损下跨度仍有区分度，保留三项。
  - TIFF 与 JPG 因位深/压缩不同，分数不可直接横比。
- **同帧**：每组 6 帧，第 k 格跨组对应同一张底片格；以基线组第 k 格为参考。

## 目录结构（仅 3 个脚本）

```
film-scan-rank/
├── SKILL.md            # 本文件
├── README.md           # 给 GitHub 读者的完整文档
├── .gitignore
├── scripts/
│   ├── rank_jpg.py     # JPG 全量+加测入口（0.70/0.15/0.15）
│   ├── rank_tiff.py    # TIFF 全量+加测入口（0.85/0.15/0.00）
│   └── rank_core.py    # 核心库：图像原语+对齐几何+三指标+Pearson朝向+process_cand+V型图（被 import）
└── scans/              # 你的扫描数据（私有，不入库）
    ├── .gitkeep
    └── README.md
```

四类功能映射：**JPG 全量+加测** → `rank_jpg.py`（一个脚本两种模式：默认全量 / `--range` 范围 / `--add` 加测）；
**TIFF 全量+加测** → `rank_tiff.py`（一个脚本两种模式：默认全量 / `--groups` 指定组 / `--add` 加测 / `--list` 列组）；
`rank_core.py` 被 import、不单独运行（原 `film_core.py` + `rank_engine.py` 已合并入此）。

## 依赖

- Python 3.8+（实测 3.13）
- `opencv-python` 或 `opencv-python-headless`（需 SIFT，cv2 4.5+ / 5.x 自带）
- `numpy`、`Pillow`（实测 numpy 2.5.2 / cv2 5.0.0）

```bash
pip install opencv-python-headless numpy pillow
```

## 前期准备（先与用户确认，勿自行假定）

开始排名前，必须先与用户完成以下**两步交互**。**不得自行假定路径，也不得擅自在本机创建目录**——
这些文件夹是在**用户自己的电脑上**按其指定位置创建的：

1. **请用户指定「用于存放扫描图像的文件夹」**（绝对路径）。
   该目录即后续 `FILM_SCAN_ROOT` 的值，每组扫描图以子文件夹形式放在其下。
   用户未明确给出前，不要开始排名，也不要替用户猜一个路径。

2. **询问用户是否「按范例生成文件夹列表」**。把下面 A–I 范例展示给用户并征询意见；
   **仅当用户明确同意后**，才在其指定的根目录下批量创建这 9 个组文件夹（空目录即可）；
   用户拒绝或未置评则不要创建。文件夹名一律用**半角**字母与下划线
   （禁用全角 Ｄ／Ｆ／Ｇ／Ｈ／Ｉ 与全角下划线 ＿，否则按名匹配会错乱）。

   ```
   <用户指定的扫描根目录>/
   ├── A_原玻璃_原片夹_0/
   ├── B_原玻璃_原片夹垫高_0.5/
   ├── C_原玻璃_原片夹垫高_1.0/
   ├── D_新玻璃_原片夹_0/
   ├── E_新玻璃_原片夹垫高_1.0/
   ├── F_新玻璃_超薄片夹_抬升0/
   ├── G_新玻璃_超薄片夹_抬升0.5/
   ├── H_新玻璃_超薄片夹_抬升0.75/
   └── I_新玻璃_超薄片夹_抬升1.0/
   ```

### 生成完毕后：展示扫描图像存放规律

文件夹创建完成后，**随即向用户展示**下面的存放规律，说明每组装什么图（**至少展示两组**）：

```
推荐按下面规律保存扫描图像

A_原玻璃_原片夹_0/          # 组 A
├── img001.tif / img002.tif ...    # TIFF 管线读 .tif/.tiff
└── img007.jpg / img008.jpg ...    # JPG 管线读 .jpg/.jpeg

B_原玻璃_原片夹_原片夹垫高_0.5/          # 组 B
├── img013.tif / img014.tif ...    # TIFF 管线读 .tif/.tiff
└── img019.jpg / img020.jpg ...    # JPG 管线读 .jpg/.jpeg

（其余组同样）
```

要点：每组放 **≥6 张** `img` 开头的扫描图；TIFF 管线只读 `.tif/.tiff`，
JPG 管线只读 `.jpg/.jpeg`；同组取**文件名倒序最新 6 张**，重扫后自动用新图。

## 数据准备

把每个「配置」放在 `FILM_SCAN_ROOT` 指向的根目录下，每个配置一个文件夹，内含 ≥6 张扫描图：

```
scans/
├── A_原玻璃_原片夹_0/     # 组 A
│   ├── img001.tif / img002.tif ...     # TIFF 管线读
│   ├── img007.jpg / img008.jpg ...     # JPG 管线读
├── B_原玻璃_原片夹垫高_1.0/      # 组 B
└── ...
```

- 文件名以 `img` 开头（epson_scan 自动生成）；同组取**最新 6 张**（按文件名倒序），重扫后自动用新图。
- TIFF 管线只认 `.tif/.tiff`，JPG 管线只认 `.jpg/.jpeg`，各自独立写盘。
- 环境变量覆盖根目录（`rank_core.py` 真实读取 `FILM_SCAN_ROOT`，未设置时默认 = 本目录下的 `scans/`）：
  `export FILM_SCAN_ROOT=/path/to/your/scans`；Windows CMD 用 `set FILM_SCAN_ROOT=D:\your\scan\root`。
  三个脚本共用这一个根目录，设置一次即可。

## 配置要排名的组

JPG 管线改 `rank_jpg.py` 的 `GROUPS`；TIFF 管线改 `rank_tiff.py` 的 `DEFAULT_GROUPS`
（组代号 → 文件夹名）与 `BASELINE`（基线组，必须为其中一组）。`CONFIG_NOTE` 仅作报告脚注。

TIFF 的 `--add` 加测组会自动写入 `scripts/groups.json`（本地文件，已被忽略），下次 `--list` / `--groups` / 全量都会带上，无需手工改 `DEFAULT_GROUPS`。

## 用法

**JPG 管线**（8bit，0.70锐 + 0.15色 + 0.15跨）—— 全量与加测同一入口：

```bash
cd scripts
python rank_jpg.py                 # 全量重算
python rank_jpg.py --range A S     # 范围重算 A→S
python rank_jpg.py --add U V       # 加测 U/V 并入既有 rank_jpg.json
python rank_jpg.py --list          # 列出已知组
```

**TIFF 管线**（48bit 彩色 TIFF / 16bit 每通道，0.85锐 + 0.15色）—— 全量与加测同一入口：

```bash
python rank_tiff.py                       # 全量重算(所有已登记组)
python rank_tiff.py --groups M O Q        # 仅重算指定组
python rank_tiff.py --list                # 列出已登记的组代号与文件夹
python rank_tiff.py --add ZI ZI_AR玻璃改_超薄片夹_3.5_M10   # 加测单组, 合并进既有结果
# 加测兼容旧写法: --add ZI --folder ZI_文件夹名 / --key ZI --folder ZI_文件夹名
```

> 加测前置: 项目目录需已有一次全量产出的 `rank_tiff.json`（加测读它并合并）；首次使用先跑全量。

产出（写入脚本同级目录）：`rank_tiff.md / .json`、`rank_tiff_vchart.png`、`rank_tiff_orient.jpg`（TIFF）；
`rank_jpg.md / .json`、`rank_jpg_vchart.png`、`rank_jpg_orient.jpg`（JPG）。

## 说明

- 加测模式与主脚本共用同一套 `_compute_cell` 计算函数，因此**全量与加测结果一致**。
- 朝向用 Pearson 8 向枚举定地面真值（SIFT 对暗/条纹/模糊内容会判错，已弃用其选向职能，仅留作几何校验）。
- **逐帧亮度/锐度波动 ≠ 底片漂移**：6 帧对应标靶 6 个不同物理区域，波动是内容/会话效应；
  判数据质量先看方向共识数与 SIFT 内点数，不要仅凭逐帧曲线下结论。
- **面板/硬件状态会污染横比**：扫描期间设备物理变化（面板变形、装片方式改变）后，不同批次组不可直接横比。
- **TIFF 与 JPG 分数不可直接横比**：位深/压缩不同导致绝对数值体系不同。
- 中文字体默认 Windows 雅黑，缺失自动回退，非 Windows 也能跑（仅图表中文可能变方框）。
- 发布版本已脱敏：扫描根目录走环境变量，不残留本地绝对路径或个人目录名。
