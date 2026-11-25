---
name: sprocket-glare-fix
description: 修复胶片扫描图中齿孔区域的眩光与齿孔带理想化重着色。基于 SIFT 对照组差分标定 + 自学习档案，支持 16bit TIFF 完整流程（齿孔带 + 成像区亚 DN 级光晕补偿）与 8bit JPG 齿孔带流程（仅齿孔带）。当用户需要去除齿孔白孔边缘光晕、把非成像区底色替换为纯色、白孔标准化、橘色边字保留、判断 JPG/TIFF 能否去光晕、或讨论光晕是否溢入成像区时使用。触发词：齿孔光晕、齿孔眩光、去光晕、齿孔带重着色、白孔发白、光晕修复、sprocket glare、齿孔白边、底片边带修复。
agent_created: true
---

# 齿孔光晕修复（TIFF 完整流程 / JPG 齿孔带流程）

## Overview

胶片扫描时齿孔白色区域会在光路中产生眩光：一是齿孔带内白孔边缘的晕圈，
二是溢入成像区、靠近齿孔一侧的弱抬升（让整幅亮度不均匀）。本 skill 固化了
一条已实测验证的处理管线，核心是**先标定、再修复、可自学习**：

```
16bit TIFF ── deglare_auto.py + 档案 ──> 齿孔带理想化 + 成像区光晕补偿 (完整)
8bit JPG  ── jpg_recolor.py          ──> 仅齿孔带理想化 (成像区不动)
```

## When to use

- 要修齿孔白孔发虚、边缘晕圈、非成像区底色不纯。
- 要把齿孔带重构成：深棕黑底 + 柔白齿孔 + 保留橘色边字。
- 判断成像区是否受光晕影响、是否需要补偿。
- 问「JPG 能不能像 TIFF 那样去光晕」——见下面适用边界，答案分层。

## 适用边界（最重要，先读）

**光晕是扫描仪光学的固定属性，与底片内容无关**——这是整套方法的前提。

| 图像类型 | 齿孔带重着色 | 成像区光晕补偿 | 原因 |
|---|---|---|---|
| **16bit TIFF** | ✅ 适用 | ✅ 适用 | 标定 amp=0.0011 → 16bit 约 **72 DN**，远高于噪声，可测可修 |
| **8bit JPG** | ✅ 适用 | ❌ 不适用 | 同样 amp 换算到 8bit 仅 **0.28 DN** < 量化步长 0.5 DN、< 噪声本底 0.9–1.2 DN，做了等于白做 |

所以：
- JPG 只做齿孔带（大色块替换，8bit 足够），绝不做成像区补偿。
- 想让成像区亮度均匀，必须用 16bit TIFF 源重新扫描/处理。
- 无齿孔的纯成像区扫描（如某些大图）本就无需处理，孔位探测会得到 1 个误检孔——
  先按 `--probe` 确认孔数 ≥ 2 再跑。

## 依赖

```bash
pip install numpy opencv-python
```

脚本对中文/含空格路径安全（用 `np.fromfile` + `cv2.imdecode`，不用 `cv2.imread`）。

## 快速开始

### A. TIFF 完整流程（16bit）

```bash
cd scripts
python deglare_auto.py "扫描件.tif" --profile 4800dpi_tiff16_nocc
```

输入也可传目录批量。默认读脚本同目录 `deglare_profiles.json` 的预置档案
（`4800dpi_tiff16_nocc` = 4800dpi / 16bit TIFF / 无色彩校正）。
**默认输出到源图所在文件夹下的 `after/` 子目录**（单文件取该文件所在目录，传目录则取该目录），
文件名 `<stem>_deglared.tif`（16bit）。需要别的落点时用 `--out <目录>` 覆盖。

常用参数：

| 参数 | 作用 |
|---|---|
| `--probe` | 只探测边界/孔位，不生成；先跑它确认孔数 ≥ 2 |
| `--profile NAME` | 指定自学习档案；不指定则用固定标定 amp=0.001 |
| `--learn-band` | 无对照组也记录带内逐孔形态，学习逐孔系数 |
| `--strength X` | 整体补偿倍率临时缩放 |
| `--no-screen` | 只修齿孔带，不动成像区 |
| `--top Y / --bot Y` | 边界探测失败时手动指定 |
| `--dark HEX` | 底色，默认 201B07 |
| `--preview` | 额外导出交界预览图 |

### B. JPG 齿孔带流程（8bit）

```bash
cd scripts
python jpg_recolor.py --src <JPG目录> --glob 'img0*.jpg'
# 或指定文件名（不含扩展名也行）
python jpg_recolor.py --src <JPG目录> img051 img055 img062
```

**默认输出到 `<JPG目录>/after/` 子目录**，需要别的落点时用 `--out <目录>` 覆盖。
输出无损 PNG（**不要用 JPG 输出**——二次压缩会在刚修干净的齿孔边缘重新引入伪影）。
白孔亮度用 `deglare_core.WHITE_LEVEL`（当前 0.96）。

### C. 有对照组时的标定与检测

自学习档案的核心：**样本池只收独立原扫描图**，修复图永远不能当样本。

```bash
# 建档（一次性）
python deglare_learn.py init --name MY_PROFILE --dpi 4800 --bits 16 --mode no-color

# 【常规路径】用带孔图 + 无孔对照追加样本：
# 自己跑 SIFT 纯平移对齐 + 差分 + 带内 lift，不依赖任何旧 JSON。新增样本一律用它。
python deglare_learn.py add --name MY_PROFILE --with A.tif --without B.tif

# 无对照组，仅学逐孔相对系数（来自带内 lift，强收缩）
python deglare_learn.py band --name MY_PROFILE A.tif

# 【异常备用】仅当新原图有问题（边界探测失败 / SIFT 内点不足 / 对照扫坏）
# 无法走 add 时，才用 references/calibration 里的旧 JSON 一次性重建档案：
python deglare_learn.py seed --name MY_PROFILE --data-dir ../references/calibration

# 查看档案 / 导出
python deglare_learn.py show --name MY_PROFILE
python deglare_learn.py export --name MY_PROFILE --out profile.json
```

效果检测（**只读，不学习、不改参数**）：

```bash
python deglare_verify.py --name MY_PROFILE --orig A.tif --fixed A_deglared.tif --ctl B.tif
python deglare_verify.py --name MY_PROFILE --batch pairs.json
python deglare_verify.py --name MY_PROFILE --report
```

> ⚠️ 职责隔离铁律：`deglare_verify.py` 只写 `verify` 记录，绝不写 `samples`、
> 绝不改 `amp_center`。把同一批图反复检测/重测当增量是循环论证，用户曾识破此错误。

## 关键参数（已标定，勿随意改）

| 参数 | 值 | 含义 |
|---|---|---|
| `WHITE_LEVEL` | 0.96 | 白孔亮度（8bit=244 / 16bit=62913）；用户对比 100/95/90 后定 96 |
| `PLAT / REACH` | 30 / 190 px | 成像区纵深：0–30px 平台，30–190px 线性归零 |
| `BOT_FACTOR` | 0.6 | 底边补偿为顶边 0.6 倍 |
| `HALF` | 170 px | 单孔横向半宽 |
| 底色 | `#201B07` | 深棕黑（早期 040008 已弃） |
| `amp_center` | 0.001103 | 由 6 对采集样本稳健学习（先验 0.001） |
| `hole_factor` | ±9% 限幅 15% | 逐孔微差，带内 lift 跨扫描均值学习，强收缩 0.35 |

## 方法论要点（踩过的坑）

1. **绝不 warp 后算指标**：插值是系统性低估锐度/光晕。对齐只求几何位移，
   差分时从原图按整数裁窗，零插值。SIFT 求纯平移（dx/dy），内点数作置信度。
2. **弱光晕必须用因果对照**：单图直接测孔对齐抬升会得到 0（底片内容噪声淹没），
   必须拿「同流程无齿孔扫描」做对照差分才能测出真实光晕。
3. **逐图 amp 不可学**：对照组逐图 amp 波动 5 倍但信噪比全 <1.5 = 噪声主导。
   改学稳健集中值 + 逐孔相对系数（带内 lift 信号强约 40 倍、跨扫描 ICC=0.69）。
4. **边界自适应**：不硬编码 y。用白孔行 + 亮度谷底探测，逐图置信度 high/medium/low。
5. **橘色边字软保留**：硬阈值会切出毛刺；用 warm mask 的 smoothstep 软混合。
6. **白孔亮度是观感参数**：不影响光晕物理，定档后可不动。

## 文件结构

```
sprocket-glare-fix/
├── SKILL.md
├── requirements.txt
├── .gitignore
├── scripts/
│   ├── deglare_core.py        # 共用图像函数（唯一口径）
│   ├── deglare_auto.py        # TIFF/JPG 通用修复入口
│   ├── deglare_learn.py       # 自学习档案 CLI
│   ├── deglare_verify.py      # 只读效果检测
│   ├── jpg_recolor.py         # JPG 齿孔带专用
│   ├── white_variant.py       # 可选：白度档位对比
│   └── deglare_profiles.json  # 预置档案（开箱即用）
└── references/
    ├── 标定数据说明.md
    └── calibration/           # 异常备用：新原图无法建档时才用 seed 读
```

## 局限

- 成像区补偿只适用于 16bit；JPG/8bit 无解（物理精度不够）。
- 无对照组时只能学逐孔相对系数，中心 amp 走预置值不漂移。
- 档案按扫描流程隔离：换 dpi / 位深 / 色彩校正 / 扫描仪必须新建档案名。
