# 齿孔光晕修复（sprocket-glare-fix）

修复胶片扫描图里**齿孔（片孔）区域的光晕**，并把齿孔带重构成干净的理想化外观。

## 它能解决什么问题

平板扫描仪扫带齿孔的胶片时，白色齿孔会在光路里产生两类问题：

1. **齿孔带内**：白孔边缘有晕圈、非成像区底色不纯（发蓝/发灰）。
2. **成像区**：靠近齿孔一侧的画面被弱光晕轻微抬亮，整幅亮度不均匀。

本工具把齿孔带重构成 **深棕黑底（#201B07）+ 柔白齿孔（96%）+ 保留橘色边字**，
并（仅 16bit TIFF）补偿成像区那部分弱光晕。

## 先看适用边界（最重要）

**前提：光晕是扫描仪光学的固定属性，与底片内容无关。** 所以它可被一次性标定、跨图复用。

| 你的图像 | 齿孔带重着色 | 成像区光晕补偿 |
|---|---|---|
| **16bit TIFF** | ✅ 可以 | ✅ 可以（补偿量约 72 DN，远高于噪声） |
| **8bit JPG** | ✅ 可以 | ❌ 不可以（补偿量仅 0.28 DN，低于量化步长和噪声，做了等于白做） |

一句话：**JPG 只能修齿孔带那一圈；想让成像区亮度均匀，必须用 16bit TIFF 源。**

另外，没有齿孔的纯成像区扫描件不需要本工具（探测会得到 1 个误检孔）；
跑之前用 `--probe` 确认孔数 ≥ 2。

## 安装

需要 Python 3 + numpy + opencv-python：

```bash
pip install numpy opencv-python
```

无需安装为 WorkBuddy skill，直接在 `scripts/` 目录下运行脚本即可。
脚本对中文/含空格路径安全。

## 怎么用

所有命令都在 `scripts/` 目录下执行。**默认输出到源图所在文件夹下的 `after/` 子目录**
（单文件取该文件所在目录，传目录取该目录本身），可用 `--out` 改。

### 场景一：16bit TIFF 完整修复（齿孔带 + 成像区）

```bash
cd scripts
python deglare_auto.py "扫描件.tif" --profile 4800dpi_tiff16_nocc
```

- 输入也可以是目录，会批量处理里面所有 `.tif/.tiff/.png/.jpg`。
- 先探测边界和孔位，再重构齿孔带、补偿成像区，输出 `<文件名>_deglared.tif`（16bit）。
- 换扫描流程（dpi / 位深 / 色彩校正 / 扫描仪）要先建新档案，见下方"自学习档案"。

常用参数：

| 参数 | 作用 |
|---|---|
| `--probe` | 只探测边界/孔位，不生成图；先跑它确认孔数 ≥ 2 |
| `--profile NAME` | 指定自学习档案；不指定则用固定标定值 |
| `--no-screen` | 只修齿孔带，不动成像区 |
| `--strength X` | 补偿倍率临时缩放 |
| `--top Y / --bot Y` | 边界探测失败时手动指定 |
| `--preview` | 额外导出交界预览图 |
| `--out 目录` | 改输出目录（默认 `<源目录>/after`） |

### 场景二：8bit JPG 齿孔带修复

```bash
cd scripts
python jpg_recolor.py --src "JPG目录" --glob "img0*.jpg"
# 或指定文件名
python jpg_recolor.py --src "JPG目录" img051 img055 img062
```

- 只重构齿孔带，成像区不动。输出无损 PNG `<文件名>_recolored.png` 到 `<JPG目录>/after/`。
- **不要改成输出 JPG**：二次压缩会在刚修干净的齿孔边缘重新引入伪影。

## 自学习档案（可选，进阶）

### 核心目标：批内稳定，批间允许波动

光晕是扫描仪光学的固定属性，但**每批扫描的强度会小幅波动，不会收敛成一个固定值**。
所以档案建模为**分层**结构：

- **批次（batch）**：一次连续扫描（连续编号、相邻文件生成时间约 5 分钟内的几张）算一批。
  同一批的光晕相同 → **批内所有图共用同一个 amp**，表现稳定一致。
- **批间**：不同批次各自标定自己的 amp，允许波动，不强行拉平到全局常数。
- **逐孔微差**：批内每个齿孔可有 ±15% 以内的系数差（带内 lift 学习，强收缩）。

简言之：**不追求全局收敛，只保证同一批内稳定**。

### 用法

```bash
# 1) 建档案（每个扫描流程一次）
python deglare_learn.py init --name MY_PROFILE --dpi 4800 --bits 16 --mode no-color

# 2) 看看这批图会被怎么分组（连续编号 + 5min 间隔 → 一批）
python deglare_learn.py batch A.tif B.tif C.tif
# 输出示例: img001_20261125: 3 张 ['A.tif','B.tif','C.tif']  ← 这个 key 就是批次名

# 3) 用「带孔图 + 无孔对照」给该批建档（--batch 用上一步的 key）
python deglare_learn.py add --name MY_PROFILE --with A.tif --without A_nohole.tif --batch img001_20261125
# 该批多对图就多跑几次 add，同一 --batch → 批内 amp 由这些样本稳健拟合

# 4) 修复时指定批次 → 整批用同一个 amp
python deglare_auto.py A.tif --profile MY_PROFILE --batch img001_20261125
python deglare_auto.py B.tif --profile MY_PROFILE --batch img001_20261125   # 同批，同一 amp

# 无对照组时，只学逐孔相对系数
python deglare_learn.py band --name MY_PROFILE A.tif

# 查看档案（列出每批 amp）
python deglare_learn.py show --name MY_PROFILE
```

未指定 `--batch` 时用档案全局 amp（旧行为兼容）；指定但档案里没该批时，回退全局并提示。
换一个批次（换了扫描时间/编号段）重新 `batch` 分组 + `add --batch` 建档即可，两批互不干扰。

**职责隔离**：`deglare_verify.py` 是只读效果检测，**不会**把修复图当样本、也不会改参数。
样本池只收未修改的原扫描图。

`references/calibration/` 里的三个旧 JSON 是**异常备用**：仅当新原图有问题
（边界探测失败 / SIFT 内点不足 / 对照扫坏）导致 `add` 走不通时，才用
`deglare_learn.py seed --data-dir ../references/calibration` 从历史数据重建档案。

## 目录结构

```
sprocket-glare-fix/
├── README.md              本文件
├── SKILL.md               WorkBuddy skill 说明（给 AI 用）
├── requirements.txt       依赖
├── scripts/               所有可执行脚本
│   ├── deglare_core.py    共用图像函数（唯一口径）
│   ├── deglare_auto.py    TIFF/JPG 通用修复入口
│   ├── jpg_recolor.py     JPG 齿孔带专用
│   ├── deglare_learn.py   自学习档案 CLI
│   ├── deglare_verify.py  只读效果检测
│   ├── white_variant.py   可选：白度档位对比
│   └── deglare_profiles.json  预置档案（开箱即用）
└── references/            标定数据与说明（异常备用）
```

## 关键参数（已标定，勿随意改）

| 参数 | 值 | 说明 |
|---|---|---|
| 白孔亮度 | 96% | 对比 100/95/90 后定 96，略柔和不过曝 |
| 底色 | #201B07 | 深棕黑 |
| 成像区纵深 | 30–190 px | 0–30px 平台，30–190px 线性归零 |
| amp_center | 0.001103 | 由 6 对对照样本稳健学习 |

## 局限

- JPG/8bit 只能修齿孔带，成像区无解（精度不够）。
- 无对照组时只能学逐孔相对系数，中心强度走预置值。
- 换扫描流程请新建档案名，不要混用。
