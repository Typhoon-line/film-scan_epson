# film-scan-rank

> 平板扫描仪（爱普生 V700 / V800 / GT-X 系列等）多配置底片扫描的 **同帧对焦 / 锐度质量排名** 工具。

把同一底片在不同扫描配置（片夹类型、垫高高度、是否 AR 玻璃、是否某道工艺等）下的扫描结果，
做 **同帧配对 → 逐组平均 → 归一排名**，用数据回答「哪一种配置对焦最准、画质最好」。

提供 **两条互相独立的排名管线**：JPG（8bit）与 TIFF（16bit），各自有 **全量重算** 与 **增量加测** 入口。

---

## 为什么需要它

胶片扫描的对焦/锐度很难用肉眼公平比较——不同配置下的扫描方向、轻微旋转、位移都会骗过眼睛。
本工具用计算机视觉做**严格同帧对齐**，然后在**原图像素**（不做任何 warp 插值）上量化三个指标，
最后以「基线配置 = 100」归一排名，结果可复现、可横比。

## 方法学（核心原则）

1. **对齐只算几何，绝不 warp 原图**
   - 8 向枚举 + Pearson 相关选原生朝向（旋转/镜像，纯像素置换，无插值）；对暗/条纹/模糊内容比 SIFT 稳健；
   - SIFT + RANSAC 求单应矩阵 H（候选 → 参考），仅用于几何质量校验；
   - H⁻¹ 把参考框（中心固定窗口）投回原图，得到 AABB 包围盒；
   - **从原图直接裁出 AABB**，再取中心固定子区域算指标。
   - 所有指标都落在原图像素上，warp 插值带来的系统性锐度低估被彻底规避。
2. **三指标**（均以基线组归一，基线 = 100）：
   | 指标 | 含义 | 口径 |
   |---|---|---|
   | `edge` 锐度 | 中心裁窗内「梯度 ÷ 局部对比度」中位数，与画面内容/曝光无关 | 内容无关 |
   | `gamut` 色域 | Lab 直方图占据格子数（色彩信息量代理） | 内容相关 |
   | `span` 跨度 | 亮度分布 p99.9 − p0.1（动态范围代理） | 物理 |

   **两条管线各有自己的总分权重**（见下文）。
3. **同帧配对**：每组 6 帧，第 k 格跨组对应同一张底片格；以基线第 k 格为参考算归一值。
4. **扫描一致性检测**：每格报告 SIFT 内点数、裁窗 Pearson 相关性（门限 >30 内点 / >0.85 相关）；
   报告含「异常单张」判定（物理分 = 锐度 + 跨度，不含色域；标记低于组均值 −8% 的单张）。

## 两条管线（TIFF / JPG）

| 管线 | 读取格式 | 总分权重 | 说明 |
|---|---|---|---|
| **TIFF** | `.tif / .tiff`（保留 16bit） | **0.85×锐度 + 0.15×色域 + 0.00×跨度** | 16bit 下跨度区分度低，从总分剔除，仅作诊断指标 |
| **JPG** | `.jpg / .jpeg`（8bit） | **0.70×锐度 + 0.15×色域 + 0.15×跨度** | 8bit 有损，跨度/动态范围仍有区分度，保留三项 |

- 两条管线共用同一套核心库（`rank_core.py`），
  只有「读取扩展名」与「总分权重」两组差异；各自写盘，产物文件名以 `-tiff` / 无后缀区分。
- **TIFF 与 JPG 因位深/压缩不同，分数不可直接横比**——请分别独立排名。

## 目录结构（仅 3 个脚本）

```
film-scan-rank/
├── SKILL.md            # 技能元数据 + 速查用法
├── README.md           # 本文
├── .gitignore
├── scripts/
│   ├── rank_jpg.py     # JPG 全量+加测入口（0.70/0.15/0.15）：默认全量；--range 范围；--add 加测
│   ├── rank_tiff.py    # TIFF 全量+加测入口（0.85/0.15/0.00）：默认全量；--groups 指定组；--add 加测；--list 列组
│   └── rank_core.py    # 核心库：读图/对齐几何/三指标 + Pearson 朝向 + process_cand + V 型图（被 import）
└── scans/              # 你的扫描数据（私有，不入库）
```

> 四类功能映射：**JPG 全量 + 加测** → `rank_jpg.py`（一个脚本两种模式）；
> **TIFF 全量 + 加测** → `rank_tiff.py`（一个脚本两种模式）；
> `rank_core.py` 是被 import 的核心库，不单独运行（由原 `film_core.py` + `rank_engine.py` 合并而来）。

## 安装

```bash
pip install opencv-python-headless numpy pillow
```

> SIFT 需 `opencv-python` 4.5+（或 5.x）；极旧版本需 `opencv-contrib-python`。
> 实测环境：numpy 2.5.2 / cv2 5.0.0（Python 3.13）。

## 准备数据

根目录下放各「配置文件夹」，每个文件夹 = 一组，内含 ≥6 张扫描图：

```
scans/
├── M_AR玻璃改_超薄片夹_1.5/
│   ├── img001.tif        # TIFF 管线读 .tif；JPG 管线读 .jpg
│   └── ...
├── O_AR玻璃改_超薄片夹_2.0/
│   └── ...
```

- 文件名以 `img` 开头；同组自动取**最新 6 张**（按文件名倒序），重扫后无需手动删旧图。
- JPG 管线只读取 `.jpg/.jpeg`；TIFF 管线只读取 `.tif/.tiff`；各自独立写盘。
- 用环境变量覆盖根目录（**代码在 `rank_core.py` 里真实读取 `FILM_SCAN_ROOT`**，未设置时默认取本目录下的 `scans/`）：
  ```bash
  # Linux / macOS
  export FILM_SCAN_ROOT=/absolute/path/to/scans
  # Windows CMD
  set FILM_SCAN_ROOT=D:\your\scan\root
  # Windows PowerShell
  $env:FILM_SCAN_ROOT = "D:\your\scan\root"
  ```
  三条管线（`rank_core` / `rank_jpg` / `rank_tiff`）共用这一个根目录，设置一次即可。

## 配置要排名的组

JPG 管线改 `rank_jpg.py` 的 `GROUPS`；TIFF 管线改 `rank_tiff.py` 的 `DEFAULT_GROUPS`：

```python
DEFAULT_GROUPS = [
    ('M', 'M_AR玻璃改_超薄片夹_1.5'),
    ('O', 'O_AR玻璃改_超薄片夹_2.0'),
    # ... 增删你的组，键为组代号、值为文件夹名
]
BASELINE = 'M'   # 基线组，必须为其中之一
```

TIFF 管线用 `--add` 加测的组会**自动落盘**到 `scripts/groups.json`，下次 `--list` / `--groups` / 全量重算都会带上，无需再手工改 `DEFAULT_GROUPS`。该文件属本地数据、已被 `.gitignore` 忽略，不随仓库分发。

## 运行

**JPG 管线**（8bit，0.70锐 + 0.15色 + 0.15跨）—— 全量与加测同一入口：

```bash
cd scripts
python rank_jpg.py                 # 全量重算所有已登记组
python rank_jpg.py --range A S     # 范围重算 A→S
python rank_jpg.py --add U V       # 加测 U/V 并入既有 rank_jpg.json
python rank_jpg.py --list          # 列出已知组
```

**TIFF 管线**（48bit 彩色 TIFF / 16bit 每通道，0.85锐 + 0.15色，跨度不计分）—— 全量与加测同一入口：

```bash
python rank_tiff.py                       # 全量重算(所有已登记组)
python rank_tiff.py --groups M O Q        # 仅重算指定组
python rank_tiff.py --list                # 列出已登记的组代号与文件夹
python rank_tiff.py --add ZI ZI_AR玻璃改_超薄片夹_3.5_M10   # 加测单组, 合并进既有结果
# 加测也兼容旧写法: --add ZI --folder ZI_文件夹名  或  --key ZI --folder ZI_文件夹名
```

> **加测前置条件**：项目目录需已有一次全量跑出的 `rank_tiff.json`（加测读它并合并）；
> 若首次使用，请先跑一次全量。加测只算新组（新组 6 帧 + 基线 M），不重算其余组。

产出（脚本同级目录）：

| 管线 | 文件 | 内容 |
|---|---|---|
| TIFF | `rank_tiff.md` / `.json` | 排名报告 / 机读数据 |
| TIFF | `rank_tiff_vchart.png` | V 型焦点模型图（总分 vs 文件夹，甜点区高亮） |
| TIFF | `rank_tiff_orient.jpg` | 各首帧原始 vs 算法校正方向核对 |
| JPG | `rank_jpg.md` / `.json` | 排名报告 / 机读数据 |
| JPG | `rank_jpg_vchart.png` | V 型焦点模型图 |
| JPG | `rank_jpg_orient.jpg` | 各首帧朝向核对 |

## 注意事项

- **全量与加测结果完全一致**：TIFF/JPG 各自的全量与加测共用一个入口、同一套计算函数（`_compute_cell`），
  加测只少算其他组、合并进既有 JSON；因此新组分数与全量重算一致。
- **加测绑定"当时的最新 6 帧"**：取帧用 `load_latest_n_tiff`（文件名倒序取最新 6 张）。
  若某组文件夹后来被追加了新扫描批次，再跑全量/加测会换用新 6 帧，分数可能与历史榜单不同——
  这不是算法变化，而是数据源变了。要复现历史分数须用当时的 6 帧。
- **面板/硬件状态会污染横比**：若扫描期间设备发生物理变化（如面板变形、装片方式改变），
  不同批次的组不可直接横比——需在相同硬件状态下重扫。
- **逐帧亮度/锐度波动 ≠ 底片漂移**：6 帧对应标靶 6 个不同物理区域，逐帧曲线有固有波动；
  判漂移必须先与基线同帧序对照。区分数据质量的可靠信号是方向共识数与 SIFT 内点数。
- **中文字体**：默认 Windows 雅黑；`rank_core._font()` 与两张联系表都有自动回退，非 Windows 也能出图（图表中文可能变方框，不影响数值）。
- **位深口径**：TIFF 以 `IMREAD_UNCHANGED` 保留 16bit/通道（48bit 彩色）；锐度/跨度在 float32 上算，色域按 16 级/轴量化时降到 8bit。文档里的「16bit」指每通道位深。
- 本仓库已脱敏：扫描根目录走 `FILM_SCAN_ROOT`（默认 `scans/`），源码内不残留本机绝对路径或个人目录。
- **TIFF 与 JPG 分数不可直接横比**：位深/压缩不同导致绝对数值体系不同，请分别排名。

## License

依据 `film-scan_epson` 仓库整体协议发布。
