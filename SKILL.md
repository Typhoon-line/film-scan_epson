---
name: film-scan-epson
description: 胶片平板扫描仪（爱普生 V700/V750/V850、GT-X800/970 等）扫描质量优化套装的入口。包含五个模块：① tilt-focus-sweep 用倾斜标靶长条扫描计算片夹最佳垫高高度；② film-scan-rank 对多配置扫描结果做同帧对齐排名、定位最佳焦平面；③ tiff-to-exr 把 Epson 48bit TIFF 线性化为 scene-linear EXR 供达芬奇/NamiColor 去色罩；④ epson_scan-auto 驱动 EPSON Scan 全自动批量扫描；⑤ sprocket-glare-fix 改善全齿孔扫描的齿孔眩光。当用户要做胶片扫描调焦、片夹垫高校准、扫描配置对比排名、扫描 TIFF 转线性 EXR、达芬奇/NamiColor 负片输入准备、自动扫描、齿孔眩光处理，或提到"胶片扫描套装"时，使用本入口并按模块路由加载对应子 skill。
agent_created: true
---

# 胶片扫描优化套装（film_scan_epson）

本仓库把胶片平板扫描的五个环节整合为**一个运行环境**：

> 调焦（找焦平面） → 排名（选最佳配置） → 自动扫描（解放双手） → 眩光处理（全齿孔） → 线性化（达芬奇/NamiColor 去色罩）

本文件是**套装入口**：负责识别需求、路由到对应模块。命中模块后，**以该模块的 `SKILL.md` 为准**执行，本文件不替代各模块的详细流程。

## 运行环境（五个模块共用依赖）

首次使用先装一次，五个模块共用：

```bash
pip install opencv-python-headless numpy pillow OpenEXR
```

- Python 3（实测 numpy 2.5.x / cv2 5.x / Python 3.13 可用）。
- SIFT 需 `opencv-python` 4.5+；极旧版本需 `opencv-contrib-python`。
- `tiff-to-exr` 模块的 EXR 写出依赖 `OpenEXR`（本模块 `tiff-to-exr/requirements.txt` 也已列）。
- 各模块都是命令行脚本，在**各自子目录内**运行；图像素材（教程图、标靶示意）也在子目录中。

## 模块路由（按需求加载，一次只读命中的那个）

| 用户意图 | 加载模块 | 入口 |
|---|---|---|
| 片夹垫高多少、找最佳焦平面、调焦卡 / 倾斜标靶长条扫描、离焦模糊 | `tilt-focus-sweep/` | `tilt-focus-sweep/SKILL.md` |
| 多组扫描结果排名、哪种片夹 / 玻璃 / 垫高最好、对焦质量横评 | `film-scan-rank/` | `film-scan-rank/SKILL.md` |
| TIFF 转线性 EXR、给达芬奇/NamiColor 准备输入、scan gamma 1.8、去色罩输入 | `tiff-to-exr/` | `tiff-to-exr/SKILL.md` |
| EPSON Scan 全自动扫描、批量扫描、告别预览等待 | `epson_scan-auto/` | 目录建设中，暂不可用 |
| 全齿孔扫描时齿孔附近眩光改善 | `sprocket-glare-fix/` | `sprocket-glare-fix/SKILL.md` |

## 模块说明

### 1. tilt-focus-sweep — 片夹垫高计算

把 135 调焦卡（USAF 1951 标靶）一端垫高扫描成长条，沿斜面均分 N 份测细密黑白锐度，反推片夹该垫多高。**两遍法**：第 1 遍粗测给出第 2 遍区间建议（X–X+1mm），第 2 遍精测给出最终推荐垫高（X.X–X.X+0.1mm），结果画在曲线图底部红字横幅。

- 入口：`tilt-focus-sweep/tilt_focus_sweep.py`
- 图文：`tilt-focus-sweep/计算垫高教程.md`

### 2. film-scan-rank — 扫描质量排名

把同一底片在不同配置下的扫描结果做**同帧配对 → 逐组平均 → 归一排名**，量化哪组对焦最准、画质最好。8 向朝向枚举 + SIFT 几何校验，**从不 warp 原图、只在原图像素上算指标**。两条独立管线：JPG（0.70 锐 + 0.15 色 + 0.15 跨）与 TIFF（0.85 锐 + 0.15 色），各有全量与加测入口。

- 入口：`film-scan-rank/scripts/rank_jpg.py` / `film-scan-rank/scripts/rank_tiff.py`
- 图文：`film-scan-rank/README.md`

### 3. tiff-to-exr — 扫描 TIFF 线性化

把 Epson Scan「无色彩校正」48bit TIFF 逆 gamma（实测 γ=1.8）转成 scene-linear
32bit float EXR，供达芬奇 + NamiColor 做负片去色罩。含读回验证（通道语义 / 精度）
与 sRGB 预览输出； gamma 标定方法见模块内 `tiff-to-exr/references/gamma标定方法.md`。

- 入口：`tiff-to-exr/epson2linear_exr.py`
- 图文：`tiff-to-exr/SKILL.md`

### 4. epson_scan-auto — 自动扫描（建设中）

基于 EPSON Scan 官方软件的全自动执行流程：一键启动、自动走完扫描、结束提醒，省去逐张预览等待。目录与脚本尚在完善，**当前遇到此类需求请如实告知用户模块未就绪，不要编造用法**。

### 5. sprocket-glare-fix — 齿孔眩光改善

处理超薄片夹全齿孔扫描时齿孔附近的眩光（扫描仪光学结构固有现象，与片夹、软件、扫描方式无关）。含核心处理脚本与自学习建档流程，按模块内 SKILL.md 执行。

- 入口：`sprocket-glare-fix/SKILL.md`
- 图文：`sprocket-glare-fix/README.md`

## 典型工作流（按序使用）

1. 装依赖 → 用 `tilt-focus-sweep` 两遍扫描定出片夹垫高值。
2. 按该高度固定片夹 → 用 `film-scan-rank` 扫多组配置并排名，选出最佳组合。
3. 配置确定后 → 用 `epson_scan-auto` 批量自动扫描（待就绪）。
4. 全齿孔扫描若有眩光 → 用 `sprocket-glare-fix` 处理。
5. 扫描好的 TIFF 若要在达芬奇 / NamiColor 去色罩 → 用 `tiff-to-exr` 转线性 EXR。

## 注意

- 资料目录 `film-holder-material/`、`scanner-material/` 不是 skill，只提供片夹设计与扫描仪资料，不要当模块加载。
- 五个模块仍可**单独安装使用**：把对应子目录复制到 `~/.workbuddy/skills/` 即可。本套装是入口，不取代它们的独立性。
- `epson_scan-auto/` 目录建好并写入 `SKILL.md` 后，请回来更新本文件的路由表与模块说明。
