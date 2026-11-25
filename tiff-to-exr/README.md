# Epson TIFF → 线性 EXR

把 Epson Scan「无色彩校正」导出的 **48bit TIFF** 转换成 **scene-linear 32bit float EXR**，供 [达芬奇](https://www.blackmagicdesign.com/products/davinciresolve) + [NamiColor](https://github.com/mrlima/namicolor) 做负片去色罩。

一句话说明白：Epson 的无色彩校正 TIFF **并不是线性数据**——gamma 编码烘在像素值里，文件里既没有 ICC 也没有 gamma 标签。而 NamiColor 要求输入必须是 scene linear。这个工具就是补上「逆 gamma」这一步，把 TIFF 变成 NamiColor 能正确吃的线性 EXR。

---

## 它能做什么 / 不能做什么

| ✅ 能 | ❌ 不能 |
|---|---|
| 把无压缩 48bit RGB TIFF 逆 gamma 成线性 EXR | 把 JPG 变成高质量线性源（8bit + DCT + 4:2:0 已毁暗部） |
| 批量处理多张 TIFF | 给已经 clipping（高光溢出）的扫描「救回」细节 |
| 生成 8bit sRGB 预览 PNG，方便人眼看 | 自动标定你的机器 gamma（需按文档手动标定） |
| 输出 32bit float TIFF 作本地备份 | 当作「扫描排名」那套嵌 ICC / sRGB 管线用（两码事，别混） |

---

## 安装

需要 Python 3，核心依赖只有三个：

```bash
pip install numpy OpenEXR Pillow
```

如果用 `--float-tiff` 额外输出 float TIFF 备份，还需要：

```bash
pip install opencv-python-headless
```

---

## 快速使用

```bash
python epson2linear_exr.py "扫描件.tif" -o "输出目录" --preview
```

一次处理多张：

```bash
python epson2linear_exr.py "a.tif" "b.tif" "c.tif" -o "输出目录" --preview
```

输出：
- `a_linear_g18.exr` —— **主交付**，给达芬奇 / NamiColor
- `a_linear_g18_preview.png` —— 给人眼看的 sRGB 预览（`--preview`）

---

## 参数

| 参数 | 说明 |
|---|---|
| `inputs` | 输入 TIFF，一个或多个（必填） |
| `-g, --gamma` | 逆 gamma 指数，默认 **1.8**（本机 V700 实测值）。范围 (0, 3] |
| `-o, --outdir` | 输出目录，默认与输入文件同目录 |
| `--preview` | 额外生成 sRGB 回编码 8bit 预览 PNG |
| `--float-tiff` | 额外生成 32bit float TIFF 备份（达芬奇对 float TIFF 无明确支持声明，只作本地备份） |
| `--no-verify` | 跳过 EXR 读回验证（默认会验证通道语义与精度） |

完整用法：

```
python epson2linear_exr.py IMG.TIF [IMG2.TIF ...] [-g 1.8] [-o OUTDIR]
                           [--preview] [--float-tiff] [--no-verify]
```

---

## 输出文件

| 文件 | 格式 | 用途 |
|---|---|---|
| `<stem>_linear_g18.exr` | 32bit float / ZIP / RGB | **主交付**，达芬奇 + NamiColor 输入 |
| `<stem>_linear_g18_preview.png` | 8bit sRGB | 只给人眼看，别拿它做去色罩 |
| `<stem>_linear_g18_float32.tif` | 32bit float TIFF | 本地计算备份（`--float-tiff`） |

生成的大体积 EXR / TIFF / PNG 默认留在本地，脚本与仓库都不打包任何扫描图。

---

## 完整工作流

### 1. 扫描端（Epson Scan 专业模式）

正片模式 / **无色彩校正** / 48bit RGB / 无压缩 / USM=无 / 自动曝光关 / 不嵌 ICC。
脚本会校验 BitsPerSample=16×3、Compression=1、Photometric=RGB、PlanarConfig=1，不符合直接报错。

### 2. 确认 gamma

默认 `1.8` 是**这台 V700 + 该驱动版本**的实测值，不是通用常数。换机器、换驱动版本或换扫描模式，请按 [`references/gamma标定方法.md`](references/gamma标定方法.md) 重新标定，再用 `-g` 传入新值。

### 3. 跑脚本

见「快速使用」。读回验证会检查三通道与原图^gamma 的相关度，应 ≈ 1.000000；若报「饱和像素」警告，说明扫描时已经 clipping，线性化救不回来。

### 4. 达芬奇 / NamiColor

1. EXR 拖入时间线，第一个节点挂 DCTL OFX → NamiColor（需 Resolve Studio v17+）。
2. 输入色彩空间下拉选 **bypass**（数据是扫描仪 RGB 未校色空间，不是 AdobeRGB / ProPhoto，前面不要加 CST）。
3. 处理模式选 **Negatives** 或 **Log-to-Log**，Input Gain 调到数值落进 0–1。
4. 逆 gamma 已在本脚本完成，NamiColor 端不要再做 gamma 修正。
5. 不要用 Lightroom / ACR 导出的 TIFF——即使标 HDR 也不是 scene linear。

---

## 常见问题

**Q：线性 EXR 拖进达芬奇怎么这么暗？**
A：正常。EXR 没有色彩标签，被按 sRGB 误读导致偏暗。给人眼看用 `--preview` 生成的 PNG，或在 CST 里设「输入 Linear → 输出 sRGB」。

**Q：gamma 为什么是 1.8，不是常见的 2.2？**
A：用同帧 VueScan Raw 对照实测：SIFT+RANSAC 配准 → 采样点投回原图零插值 → 数十万点 log-log 拟合，斜率=1 落在 g≈1.80（g=2.2 时斜率 1.213）。详见 [`references/gamma标定方法.md`](references/gamma标定方法.md)。基于 2.2 的旧文件已作废。

**Q：线性化会丢信息吗？**
A：逆 gamma 本身可逆、不丢精度；但补不回扫描时已经丢的（clipping 高光、JPG 有损压缩）。中间计算与存档一律 float32 / EXR，别用 16bit PNG 存线性数据（会并阶暗部）。

---

## 文件结构

```
tiff-to-exr/
├── README.md                   本文件：给人看的作用与用法
├── SKILL.md                    给 agent 看的触发条件与工作流
├── epson2linear_exr.py         主脚本：解析 TIFF IFD → 逆 gamma → 写 EXR + 验证
├── requirements.txt            依赖清单
├── .gitignore                  排除生成的大文件与 Python 缓存
└── references/
    └── gamma标定方法.md         gamma=1.8 实测推导与复标定方法
```

## 隐私

本工具只含方法学代码与文档，不含任何个人扫描图、EXR 或标注文件。发布前请确认无个人信息、绝对路径、设备序列号或私人照片。
