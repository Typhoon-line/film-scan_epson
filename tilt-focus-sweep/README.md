# 倾斜标靶焦平面扫描 · 片夹垫高计算（tilt-focus-sweep）

利用 135 调焦卡（USAF 1951 黑白标靶）长条扫描图，计算胶片扫描仪片夹的最佳垫高高度，  
消除因片夹与玻璃不平行导致的离焦模糊。支持**两遍精测法**把定位精度提升到约 0.027mm/份。

> 本项目以扫描对焦评估工作流沉淀而来，配套操作示意图见本目录（`tilt-focus-sweep/`）下的 `*.jpg`。

## 目录结构

<span style="background-color:rgb(243, 245, 247)">127.0.0.1:49916</span>

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
# 第一遍（粗）：高低差 4mm，均分 45 份
python tilt_focus_sweep.py <长条扫描图.jpg> --lift-mm 4 --n 45

# 第二遍（精）：以第一遍结果 d 为中心、±0.2mm 对称倾斜，低端垫高 = d-0.2
python tilt_focus_sweep.py <新长条扫描图.jpg> --lift-mm 0.4 --n 15 --low-lift-mm <d-0.2>
```

输出：

- `tilt_sweep_profile.png`：沿长条的锐度/色差/综合曲线 + 窗口标记
- `tilt_best_window.jpg`：原图全宽回截最佳高度区间（红框圈最高份）
- 控制台打印：最佳份 Y、焦平面高度 Zf、统一垫高、绝对最佳高度

## 核心公式

```
Zf = X × Y / n
```

- X：高低端垫高差(mm)；n：长条均分份数；Y：综合锐度最高份序号(1..n)
- 四个支脚统一垫起 `Zf` → 片夹整体平行于玻璃
- 第二遍 `--low-lift-mm` 填入低端绝对垫高后，脚本直接输出**绝对最佳高度 = 低端垫高 + Zf**

## 两遍精测法（推荐）

| 遍次       | 高低差 X        | 份数 n | 目的            | 分辨率       |
| -------- | ------------ | ---- | ------------- | --------- |
| 第 1 遍（粗） | 4mm          | 45   | 大跨度覆盖峰值，粗定位 d | 0.089mm/份 |
| 第 2 遍（精） | 0.4mm（d±0.2） | 15   | 把最佳点放长条正中，精定位 | 0.027mm/份 |

垫片规格：0.2mm 易得，0.1mm 较难但可买到；按 `d−0.2 / d+0.2` 组合即可逼近任意目标高度。

## License

脚本以 MIT 许可证发布（如需变更请在本仓库补充说明）。操作示意图版权归原作者所有，  
转载使用前请取得授权。
