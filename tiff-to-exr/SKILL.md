---
name: tiff-to-exr
description: 把 Epson Scan「无色彩校正」输出的 48bit TIFF 线性化为 scene-linear 32bit float EXR，供达芬奇 / NamiColor 做负片去色罩。当用户需要把扫描 TIFF 转成线性 EXR、给 NamiColor / 达芬奇准备输入、讨论扫描 gamma 编码（1.8 vs 2.2）、对比 Epson TIFF 与 VueScan Raw、判断 JPG/PNG/TIFF 能否线性化时使用。触发词：TIFF 转 EXR、线性化、scene linear、NamiColor、达芬奇负片输入、gamma 1.8、去色罩输入、Epson 无色彩校正。
agent_created: true
---

# Epson TIFF → 线性 EXR（达芬奇 / NamiColor 输入准备）

## Overview

Epson Scan「无色彩校正」导出的 48bit TIFF **不是线性数据**：gamma 编码烘在像素值里，
文件里既无 ICC 也无 gamma 标签。NamiColor 要求输入必须是 scene linear，
因此必须先逆 gamma 再交给它。本 skill 固化了这条已实测验证的管线：

```
Epson 48bit TIFF (gamma 编码, 无标签)
   → 逆 gamma 1.8 (float32)
   → scene-linear EXR (ZIP / RGB)  ──→ 达芬奇 + NamiColor
```

## When to use

- 用户要把 Epson Scan 扫的 TIFF 转成线性 EXR，给达芬奇 / NamiColor 用。
- 用户问某张扫描 TIFF / PNG / JPG 是否线性、带不带 gamma、能否直接进 NamiColor。
- 用户要对比 Epson TIFF 与 VueScan Raw 的像素关系、标定扫描 gamma。
- 用户讨论负片去色罩的输入格式、为什么线性化后"看着很暗"。

## 依赖安装

```bash
pip install numpy OpenEXR Pillow
```

核心依赖只有 `numpy` + `OpenEXR` + `Pillow`（预览 PNG 用）。`--float-tiff` 可选功能
额外需要 `opencv-python-headless`。脚本对中文/含空格路径安全（ASCII 路径直写，
非 ASCII 路径自动经临时文件中转，规避 OpenEXR C++ 后端的路径问题）。

## 快速开始

```bash
python epson2linear_exr.py "扫描件.tif" -o "输出目录" --preview
```

输出：
- `<stem>_linear_g18.exr` —— **主交付**，32bit float / ZIP / RGB，给达芬奇 / NamiColor。
- `<stem>_linear_g18_preview.png` —— sRGB 回编码 8bit 预览，**只给人眼看**（`--preview`）。
- `<stem>_linear_g18_float32.tif` —— 本地计算备份（`--float-tiff`；达芬奇对 float TIFF
  无明确支持声明，别拿它赌兼容性）。

默认 `gamma=1.8`（本机实测值），`-g` 可覆盖；默认读回 EXR 验证通道语义与精度，
`--no-verify` 可跳过。支持一次传多个 TIFF 批量处理。

## 工作流

1. **确认输入符合扫描 SOP**：Epson Scan 专业模式 / 正片模式 / 无色彩校正 /
   48bit RGB / 无压缩 / USM=无 / 自动曝光关 / 不嵌 ICC。脚本会校验
   BitsPerSample=16×3、Compression=1、Photometric=RGB、PlanarConfig=1，
   不符合直接报错。
2. **确认 gamma 值**：默认 1.8 仅在被测机器 + 该驱动版本实测成立。
   换机器 / 换驱动 / 换扫描模式，按 `references/gamma标定方法.md` 重新标定，
   再用 `-g` 传入新值。
3. **跑脚本**：见快速开始。读回验证三通道与原图^gamma 的相关应 ≈1.000000；
   若报警"饱和像素"说明扫描时已 clipping，线性化救不回来。
4. **进达芬奇**：EXR 拖入时间线 → 第一个节点挂 DCTL OFX → NamiColor
   （需 Resolve Studio v17+）；输入色彩空间下拉选 **bypass**（数据是扫描仪 RGB
   未校色空间，不是 AdobeRGB / ProPhoto，前面不要加 CST）；处理模式选
   Negatives 或 Log-to-Log，Input Gain 调到数值落进 0–1。
5. **不要混管线**：这条 EXR 管线与「扫描排名」用的嵌 ICC / sRGB TIFF 管线
   是两种用途，不要拿同一组数互推。

## 关键铁律（踩过的坑）

- **gamma 不是 2.2 而是 1.8**（这台 V700 + 该驱动实测）。同帧对照用 SIFT+RANSAC 求几何
  变换、采样点投回两张原图取局部均值（零插值），log-log 斜率扫描定出 g=1.80。
  之前基于 2.2 的文件已作废。详见 `references/gamma标定方法.md`。
- **通道顺序**：`np.fromfile` 读出是 RGB；用 `cv2.imwrite` 写文件必须先
  `arr[...,[2,1,0]]` 转 BGR；写 EXR 用通道名 R/G/B 直接给 RGB 语义、不交换。
  曾因这一步写反导致"看着像反相"的误判。
- **线性 EXR 看着暗是正常的**：无标签被按 sRGB 误读。给人眼看用 `--preview`
  生成的 sRGB 回编码 PNG，或在 CST 里设 输入 Linear → 输出 sRGB。
- **线性化不丢信息，但补不回已丢的**：逆 gamma 可逆；但 16bit 存线性会并阶暗部、
  JPG 的 8bit+DCT+4:2:0 在编码时就毁了暗部精度。中间计算与存档一律 float32/EXR。
- **禁用 LR/ACR 导出的 TIFF**：即使标 HDR 也不是 scene linear，NamiColor 官方明确禁止。

## 配套文件

| 文件 | 作用 |
|---|---|
| `epson2linear_exr.py` | 主脚本：解析 TIFF IFD → 逆 gamma → 写 EXR + 读回验证 |
| `references/gamma标定方法.md` | gamma=1.8 的实测推导与复标定方法（含阶梯楔法） |
| `SKILL.md` | 本文件：触发条件与工作流 |

## 隐私与发布

脚本、文档只含方法学，**不得打包任何用户自己的扫描图、EXR、标注文件**。
发布前确认无个人用户名、主机名、文件管理器绝对路径、设备序列号、私人照片。
生成的大体积 EXR / TIFF / PNG 留本地，不入库。
