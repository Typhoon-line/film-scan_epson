#!/usr/bin/env python3
"""
片夹一端垫高 · 倾斜标靶长条扫描 焦平面扫描 (tilt focus sweep)
==============================================================
场景: 把 USAF 1951 标靶菲林夹在片夹里, 一端垫高、一端不垫 → 标靶相对扫描仪
玻璃板呈一个斜面。扫描后得到一张"长条图像": 沿斜面方向(长条长轴)不同位置
离焦程度不同, 其中某一段恰好落在扫描仪最佳焦平面 → 该段锐度最高。

本工具复用 focus_compare.py 的 `calculate_sharpness`(锐度)。色差 = 纵向色差 LoCA:
把彩色 strip 拆成 R/G/B 三通道各自跑锐度曲线, 各通道峰值份 Y_c → Z_c = X × Y_c / n,
通道间焦平面高度差即 LoCA(不再使用横向 R-B 边缘错位 / 色偏)。严格按以下 7 步处理:

  1. 传入单张标靶 jpg, 并询问"片夹一端垫高了多少 mm"(--lift-mm)
  2. crop_center_strip: 从中轴裁中央 80% 宽中心竖条(只锁标靶主体, 排除画幅差异);
     支持固定像素(>=1)或比例(0-1, 默认0.8 = 中央 80% 区域)
  3. 竖条按高度均分成 n 份(默认40), 每份 (整条宽) × (总高/n)
  4. 逐份 calculate_sharpness → sharp(灰度主锐度, 唯一评分量)
  5. 逐份把彩色 strip 拆成 R/G/B 三通道, 各跑同一锐度算法 → sharp_R/G/B;
     各通道按与灰度相同的"归一化平顶容差 + 中位索引"选峰值份 Y_c,
     Z_c = X × Y_c / n (低端垫高参与时为 low + Z_c), 通道间高度差 = 纵向色差 LoCA。
     **LoCA 只作诊断展示, 不参与评分**(它是焦平面位置差, 不是逐份质量分)。
  6. 综合分 = 灰度锐度 sharp 本身, 排名(色差不再参与评分)。
    7. 取综合最高那一份, 连同序号前5/后5(共11份)的高度数据, 回原图截取这一段
     (100% 画质, 红框圈出最高份), 并算出"最佳锐度应垫高高度"
     注: 若综合最高份 Y 靠近长条两端、不满足"前5/后5"时, 不足的一侧直接截断
     (窗口随之变窄, 不越界)。例: n=40、最高份为第3份(Y=3) → 截取第1–8份(0-7)。

  物理垫高公式(用户指定): 设片夹高/低两端垫高差 = X(mm), 长条均分 n(默认40) 份,
  扫描得到综合锐度最高的份序号 = Y(1..n), 则
      → 四个支脚统一垫起高度 = X × Y / n
  (即: 焦平面高度 Zf = X × Y / n; 整片平行于玻璃即全部落入焦平面)

  两遍测试(固定流程, 用户先后提交 2 张 TIFF):
    第1遍(粗, 第1张 TIFF): --lift-mm 4 --n 40
        → 图底部红字 + 控制台 = 第2遍区间建议 (跨度1mm)
          常规: 以峰值Zf居中 → 例 Zf=3.9(第39份) → 3.4–4.4mm
          特例: 峰值恰贴最后一份(第40份), Zf=4.0 → 从Zf起向上外探 4–5mm
                (Zf 已是第一遍实测最大值, 真实最佳可能在更高处, 居中会漏掉上方)
        → 分辨率 4/40 = 0.100 mm/份
    第2遍(精, 第2张 TIFF): 按建议区间装夹, --lift-mm 1.0 --n 10 --low-lift-mm <lo>
        → 图底部红字 + 控制台 = 最终推荐垫高 X.X–X.X+0.1mm (四脚统一垫到该区间)
        → 分辨率 1.0/10 = 0.100 mm/份
    判定: 显式传 --low-lift-mm(即使为0)=第2遍; 不传=第1遍。
    注意: 第1遍区间以Zf居中 → 第2遍峰值应落中间段(第3–8/10份);
          若仍贴边说明第一遍区间偏了, 按新Zf重设区间重扫。

用法:
  python tilt_focus_sweep.py <长条扫描单图或文件夹> [--lift-mm 1.25]
      [--strip-mm <长条物理长度mm>] [--low-lift-mm <低端绝对垫高>] [--w-sharp 1.0]
      [--n 40] [--ksize 3] [--strip 0.8] [--out .]

  --lift-mm   片夹"高的一端"相对"低的一端"垫高的毫米数(高低差 X, 用于换算物理垫高量)
  --strip-mm  长条沿斜面方向的物理长度(mm)(用于把峰值位置换算成 mm)
  --low-lift-mm  低端绝对垫高 mm; 显式传入(即使为0)即判定为第2遍, 输出最终推荐 X–X+0.1mm
  --w-sharp  综合分中锐度的权重(默认1.0; 色差固定不参与评分)

装载约定(固定): 标靶菲林垫付高的一端 = 菲林上序号小的一端; 扫描后图像
  顶部即 = 低端(不垫高那端)。故"份 #1"恒为图像顶部 = 低端, Y 直接从图像
  顶部数起, 无需区分扫描装入方向。

输出(默认写脚本同目录):
  tilt_sweep_profile.png      双面板曲线 + 11份窗口
                              上: 综合分(=灰度锐度) 主曲线, 红点=最高份
                              下: R/G/B 三通道锐度曲线(诊断, 不进分) + 彩色峰值点,
                                  底部红字 = 纵向色差 LoCA (Z_R/Z_G/Z_B 高度差)
  tilt_best_window.jpg        原图回截的 11 份区间(100% 画质, 全宽, 红框圈最高份)
  (逐份灰度 sharp + 三通道 LoCA + 排名 + 物理垫高量 直接打印在控制台)
"""

import os
import sys
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
for _p in (HERE, PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import cv2
from focus_compare import (
    calculate_sharpness,
    crop_center_strip, imread_safe, list_images,
)


# ===================== 切片(7步 2/3) =====================
def build_strip(path, strip_w=0.8, n_parts=40):
    """载入单图 → 裁中央 80% 宽中心竖条 → 均分 40 份。

    strip_w: 1.0 及以上为像素宽, 0-1 为图像宽度比例(默认 0.8 = 中央 80% 区域)。

    装载约定(固定): 图像顶部 = 低端(不垫高那端), 故"份 #1"恒为图像顶部,
    序号 Y 直接从图像顶部数起, 与扫描装入方向无关。

    返回 dict:
      img_color / img_gray : 原图(彩色/灰度, 全幅, 用于第7步回截)
      strip_color / strip_gray : 中心竖条
      strip_x0 : 竖条在原图的 x 起点(用于回截)
      part_h : 每份高度(px, 浮点)
      edges : (n_parts+1,) 各份 y 边界(浮点, 竖条同源)
      centers : (n_parts,) 各份中心 y(px)
      H : 竖条高度
    """
    img_color = imread_safe(path, cv2.IMREAD_COLOR)
    if img_color is None:
        return None
    # 保证"倾斜轴"落在竖条高度方向: 仅当原图是横条(宽>高)时旋转成竖条。
    # 若原图已是竖条(高>宽, 如长条标靶 4817×42572), 不旋转,
    # 中心竖条的高度即倾斜长轴, 与"沿竖条高度均分"一致。
    if img_color.shape[1] > img_color.shape[0]:
        img_color = cv2.rotate(img_color, cv2.ROTATE_90_CLOCKWISE)
    img_gray = cv2.cvtColor(img_color, cv2.COLOR_BGR2GRAY)

    strip_color = crop_center_strip(img_color, width=strip_w)
    strip_gray = cv2.cvtColor(strip_color, cv2.COLOR_BGR2GRAY)
    H = strip_gray.shape[0]
    W = strip_gray.shape[1]
    strip_x0 = (img_color.shape[1] - W) // 2   # 竖条在原图的 x 起点

    edges = np.linspace(0, H, n_parts + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    return {
        "img_color": img_color, "img_gray": img_gray,
        "strip_color": strip_color, "strip_gray": strip_gray,
        "strip_x0": strip_x0, "part_h": H / n_parts,
        "edges": edges, "centers": centers, "H": H, "W": W,
    }


def _subpix_peak(mag, i, half=3):
    """在 mag 第 i 点左右找峰, 抛物线拟合出亚像素位置。mag 是 1D 梯度幅值。"""
    lo = max(1, i - half)
    hi = min(len(mag) - 2, i + half)
    if hi <= lo:
        return float(i)
    seg = mag[lo:hi + 1]
    j = int(np.argmax(seg)) + lo
    v0, v1, v2 = float(mag[j - 1]), float(mag[j]), float(mag[j + 1])
    den = v0 - 2.0 * v1 + v2
    off = 0.5 * (v0 - v2) / den if abs(den) > 1e-9 else 0.0
    return float(j + max(-1.0, min(1.0, off)))


def ca_edge_shift(bgr, pct=99.0, half=3, max_edges=2500, seed=0):
    """[已弃用, 主流程不再调用; 保留作独立工具] 横向色散 = R/B 边缘错位 (px)。

    主流程色差已改为纵向色差(LoCA): 三通道锐度峰值份差换算高度差。

    为什么不用 chroma/|∇|: 那个比值在弱边缘上被"固定噪声底 ÷ 小梯度"主导,
    越模糊的段读数越虚高, 实测同一批数据只改掩膜松紧就能从 0.92 变到 0.29,
    趋势符号还会翻转 —— 它测的是"模糊程度", 不是色散。

    本法为什么天然归一:
      - 亮度: 边缘位置不随亮度移动 -> 免疫
      - 对比度: 抛物线拟合对幅值缩放不敏感 -> 免疫
      - 色偏: 位置与偏色无关 -> 免疫
      单位直接就是 px, 不需要再除任何东西。

    做法:
      1) 取 R/B 两通道的水平梯度 |∂I/∂x|, 相加作为"边缘强度"定位候选边缘(局部极大);
      2) 只保留最强的一批(默认前 1%), 弱边缘的亚像素估计不可靠;
      3) 对每条边缘, 分别在 R 和 B 通道做抛物线拟合求峰位, 得 Δ = x_R - x_B;
      4) 返回 Δ 的**有符号中位数**(不要用 |Δ| 中位数, 噪声会系统性放大)。

    返回 (signed_median, median_abs, n_edges)。
    """
    bgr = bgr.astype(np.float32)
    b = bgr[:, :, 0]
    r = bgr[:, :, 2]
    gxR = np.abs(cv2.Sobel(r, cv2.CV_32F, 1, 0, ksize=3))
    gxB = np.abs(cv2.Sobel(b, cv2.CV_32F, 1, 0, ksize=3))
    gsum = gxR + gxB

    thr = float(np.percentile(gsum, pct))
    if not np.isfinite(thr) or thr <= 0:
        return float('nan'), float('nan'), 0

    lm = np.zeros(gsum.shape, dtype=bool)
    lm[:, 1:-1] = ((gsum[:, 1:-1] >= gsum[:, :-2]) &
                   (gsum[:, 1:-1] >= gsum[:, 2:]))
    cand = (gsum > thr) & lm
    ys, xs = np.nonzero(cand)
    if len(ys) == 0:
        return float('nan'), float('nan'), 0
    if len(ys) > max_edges:
        k = np.random.RandomState(seed).choice(len(ys), max_edges, replace=False)
        ys, xs = ys[k], xs[k]

    W = gsum.shape[1]
    d = []
    for yy, xx in zip(ys, xs):
        if xx < half + 1 or xx >= W - half - 1:
            continue
        d.append(_subpix_peak(gxR[yy], xx, half) - _subpix_peak(gxB[yy], xx, half))
    if len(d) < 30:
        return float('nan'), float('nan'), len(d)
    d = np.asarray(d, dtype=np.float64)
    return float(np.median(d)), float(np.median(np.abs(d))), int(len(d))


def measure_parts(S, ksize=3):
    """7步 4/5: 逐份算灰度 sharp(唯一评分量) + R/G/B 三通道 sharp(纵向色差用)。

    一次循环同时算灰度与三通道, 复用同一份 edges/同一套 calculate_sharpness。
    返回 (sharp, pc, res, nedge):
      sharp  (n,) 灰度锐度
      pc     {'R':(n,),'G':(n,),'B':(n,)} 三通道锐度(cv2 BGR 索引 2/1/0)
      res    (n,) 分辨率
      nedge  (n,) 边缘数
    """
    edges = S["edges"]
    strip_gray = S["strip_gray"]
    strip_color = S["strip_color"]
    n = len(edges) - 1
    sharp, res, nedge = [], [], []
    pc = {'R': [], 'G': [], 'B': []}
    for i in range(n):
        y0, y1 = int(round(edges[i])), int(round(edges[i + 1]))
        if y1 - y0 < 16:
            for lst in (sharp, res, nedge, pc['R'], pc['G'], pc['B']):
                lst.append(float('nan'))
            continue
        gp = strip_gray[y0:y1, :]
        cp = strip_color[y0:y1, :]
        try:
            sh, _n, _m, _mask, _g, rl = calculate_sharpness(gp, ksize=ksize)
            sharp.append(float(sh)); res.append(float(rl)); nedge.append(float(_n))
        except Exception:
            for lst in (sharp, res, nedge):
                lst.append(float('nan'))
        for ci, name in ((2, 'R'), (1, 'G'), (0, 'B')):   # cv2 BGR
            try:
                v, *_ = calculate_sharpness(cp[:, :, ci], ksize=ksize)
                pc[name].append(float(v))
            except Exception:
                pc[name].append(float('nan'))
    return (np.array(sharp, float),
            {k: np.array(v, float) for k, v in pc.items()},
            np.array(res, float), np.array(nedge, float))


# ===================== 综合锐度(7步 6) =====================
def comprehensive(sharp, w_sharp=1.0):
    """综合分 = 灰度锐度 min-max 归一化到 0..1。色差(LoCA)固定不参与评分。"""
    s = sharp.astype(float).copy()
    smin, smax = np.nanmin(s), np.nanmax(s)
    sN = np.full_like(s, 0.5)
    if smax - smin > 1e-12:
        sN = (s - smin) / (smax - smin)
    comp = w_sharp * sN
    return comp, sN


# ===================== 回截窗口(7步 7 邻域) =====================
WIN_HALF = 5   # 最高份前/后各取 5 份 → 理想窗口 2*WIN_HALF+1 = 11 份

def clamp_window(k, half, n):
    """以综合最高份 k(0-based) 为中心, 取 [k-half, k+half] 共 2*half+1 份。

    靠近长条两端时, 不足的一侧直接截断(不越界), 窗口随之变窄:
      - k 偏小(靠近图像顶部=低端): idx_lo 夹到 0
      - k 偏大(靠近图像底部=高端): idx_hi 夹到 n-1
    例: n=40, 最高份 k=2(Y=3, 1-based) → (0, 7); k=3(Y=4) → (0, 8)。
    若 n 本身 ≤ 2*half+1, 则返回整条 [0, n-1]。
    返回 (idx_lo, idx_hi) 含两端, 0-based。
    """
    if n <= 0:
        return 0, -1
    k = max(0, min(k, n - 1))
    idx_lo = max(0, k - half)
    idx_hi = min(n - 1, k + half)
    return idx_lo, idx_hi


def window_truncated(k, half, n):
    """返回 (截断于低端?, 截断于高端?) 两个布尔, 用于输出/JSON 提示。"""
    if n <= 0:
        return False, False
    return (k - half) < 0, (k + half) > (n - 1)


# ===================== 物理垫高量(7步 7) =====================
def physical_height(frac_best, lift_mm, strip_mm=0.0, low_lift_mm=0.0):
    """由最佳份序号 Y 推算垫高量(用户指定公式)。

    设片夹高的一端相对低的一端垫高 X = lift_mm(mm), 长条均分 n 份,
    扫描得到综合锐度最高的份序号为 Y(1..n), 则
        frac_best = Y / n
    本实验真正测到的扫描仪焦平面高度(相对低端):
        Zf = X × Y / n
    - 四个支脚**统一垫起**高度(使片夹整体平行于玻璃、整片落入焦平面)
      = Zf = X × Y / n   ← 用户指定公式
    - 若保持倾斜、想把"最清晰段"移到长条正中(最大化景深覆盖),
      需把低端再调整 d = X × (Y/n − 0.5) mm (Y/n>0.5 垫高, <0.5 降低低端)。
    - 第二遍精测时传入 low_lift_mm(低端绝对垫高), 则:
        绝对最佳高度 = 低端垫高 + Zf   (= 你最终应统一垫到的高度, 无需心算)
    """
    Zf = frac_best * lift_mm
    L_opt = lift_mm * (frac_best - 0.5)   # 可正(垫高)可负(降低低端)
    abs_best = low_lift_mm + Zf           # 绝对最佳高度(第二遍用)
    out = {
        "focal_plane_height_mm": round(Zf, 4),
        "uniform_lift_all_feet_mm": round(Zf, 4),   # 相对低端 = X × Y / n
        "abs_best_height_mm": round(abs_best, 4),   # 绝对 = 低端垫高 + Zf
        "low_lift_mm": round(low_lift_mm, 4),
        "tilt_adjust_to_center_best_mm": round(L_opt, 4),
        "best_pos_frac_from_low": round(frac_best, 4),
    }
    if strip_mm > 0:
        out["best_pos_mm_from_low"] = round(frac_best * strip_mm, 3)
    return out


def recommend_fine_window(Zf, span=1.0, at_top_edge=False):
    """第一遍粗测后, 由 Zf 给出第二遍精测的垫高区间(默认跨度 1mm)。

    两种规则:
      1) 常规(峰值在中间份, 含贴最低端): 以 Zf 居中 → [Zf-span/2, Zf+span/2]
         - 峰值 Zf 落在第二遍正中, 已测/未测各覆盖一半。
         - 低端垫高不得为负: lo 若 <0 则钳到 0, 缺口向后补, 跨度保持 span。
           例: Zf=3.9 → 3.4–4.4mm; Zf=0.25 → 0–1mm。
      2) 峰值恰贴第一遍最后一份(最高端, at_top_edge=True): 从 Zf 起向上外探
         → [Zf, Zf+span]。此时的 Zf 已是第一遍实测最大值, 真实最佳可能在
         实测范围之外(更高), 若仍居中会漏掉上方, 故整段向高端外推。
         例: Zf=4.0(第40份) → 4–5mm。仅此一种贴边情形外推, 其余一律居中。
    第二遍: 低端垫 lo, 高端垫 hi, 跨度 span(mm), 均分 n 份。
    返回 (lo, hi), 单位 mm。
    """
    if at_top_edge:
        lo = Zf
    else:
        lo = Zf - span / 2.0
        if lo < 0:
            lo = 0.0
    lo = round(float(lo), 4)
    hi = round(lo + span, 4)
    return lo, hi


def recommend_final_lift(abs_best, step=0.1):
    """第2遍精测后, 由绝对最佳高度给出最终推荐垫高区间(默认宽 0.1mm)。

    规则: lo = floor(abs_best/step)*step, hi = lo + step
      - 与第2遍分辨率一致(跨度1mm/10份 = 0.1mm/份)
      - 用户手上有 0.1mm 垫片, 故推荐到 0.1mm 网格; 四脚统一垫到 [lo, hi] 即可
    返回 (lo, hi), 单位 mm。
    """
    import math
    lo = round(math.floor(abs_best / step + 1e-9) * step, 4)
    hi = round(lo + step, 4)
    return lo, hi


# ===================== 可视化 =====================
def imwrite_safe(path, img, quality=95):
    ext = os.path.splitext(path)[1].lower() or '.jpg'
    if ext in ('.jpg', '.jpeg'):
        ok, buf = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    else:
        ok, buf = cv2.imencode(ext, img)
    if not ok:
        return False
    with open(path, 'wb') as f:
        f.write(buf.tobytes())
    return True


def make_chart(sharp, pc, peaks, comp, k, idx_lo, idx_hi,
               n, out_path, title, fine=None, final=None, fine_note=""):
    """双面板曲线图。

    上面板 = 综合分(=灰度锐度, 黑粗) + 最高份红点 + 11份窗口绿底。
    下面板 = 诊断(不进分): R/G/B 三通道锐度曲线 + 各自峰值点标注(不画 LoCA)。
    """
    import math
    from PIL import Image, ImageDraw, ImageFont
    base_h = 820                      # 图表本体高度(不含底部建议横幅)
    W, H = 1120, base_h + (78 if (fine is not None or final is not None) else 0)
    img = Image.new('RGB', (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 18)
        fonB = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 20)
        font_s = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 13)
        font_xs = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 12)
        font_big = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 30)
    except Exception:
        font = fonB = font_s = font_xs = ImageFont.load_default()
        font_big = ImageFont.load_default()

    x0, x1 = 82, W - 82
    A_t, A_b = 72, 448          # 上面板
    B_t, B_b = 528, 742         # 下面板

    def px(v):        # 份序号(1..n) → x
        return x0 + (v - 1) / max(1, n - 1) * (x1 - x0)

    def dashed(pts, color, width=2, dash=12, gap=7):
        for i in range(len(pts) - 1):
            ax, ay = pts[i]; bx, by = pts[i + 1]
            dx, dy = bx - ax, by - ay
            L = math.hypot(dx, dy)
            if L == 0:
                continue
            ux, uy = dx / L, dy / L
            t = 0.0
            while t < L:
                t2 = min(t + dash, L)
                d.line([(ax + ux * t, ay + uy * t),
                        (ax + ux * t2, ay + uy * t2)], fill=color, width=width)
                t += dash + gap

    def yof(v, lo, hi, top, bot):
        if hi - lo < 1e-12:
            return (top + bot) / 2
        return bot - (v - lo) / (hi - lo) * (bot - top)

    # ---------- 标题 ----------
    d.text((x0, 26), title, fill=(0, 0, 0), font=fonB)
    d.text((x0, 50), "上：灰度锐度得分（越高越好，红点=最高）    "
                     "下：R/G/B 三通道锐度曲线（诊断，不计分）",
           fill=(110, 110, 110), font=font_s)
    # ---------- 窗口绿底(两面板都覆盖) ----------
    d.rectangle([px(idx_lo + 1) - 5, A_t, px(idx_hi + 1) + 5, B_b], fill=(226, 244, 226))

    # ================= 上面板: 综合分 =================
    cvals = comp[np.isfinite(comp)]
    c_lo, c_hi = float(cvals.min()), float(cvals.max())
    pad = max(0.06, (c_hi - c_lo) * 0.15)
    a_lo, a_hi = c_lo - pad, c_hi + pad
    smin, smax = float(np.nanmin(sharp)), float(np.nanmax(sharp))

    def score_label(v):
        """归一化刻度换算回锐度原始分, 更直观。"""
        if smax - smin > 1e-12:
            return f"{smin + v * (smax - smin):.4f}"
        return f"{v:.2f}"

    for i in range(5):                      # 网格 + 左轴刻度
        v = a_lo + (a_hi - a_lo) * i / 4
        y = yof(v, a_lo, a_hi, A_t, A_b)
        d.line([(x0, y), (x1, y)], fill=(232, 232, 232))
        d.text((10, y - 8), score_label(v), fill=(120, 120, 120), font=font_xs)
    d.line([(x0, A_t), (x0, A_b)], fill=(120, 120, 120))
    d.line([(x0, A_b), (x1, A_b)], fill=(120, 120, 120))
    d.text((10, A_t - 28), "锐度分", fill=(60, 60, 60), font=font_s)

    pts = [(px(i + 1), yof(comp[i], a_lo, a_hi, A_t, A_b))
           for i in range(n) if np.isfinite(comp[i])]
    if len(pts) > 1:
        d.line(pts, fill=(25, 25, 25), width=3)

    # 最高份标记
    by = yof(comp[k], a_lo, a_hi, A_t, A_b)
    d.line([(px(k + 1), A_t), (px(k + 1), B_b)], fill=(210, 40, 40), width=2)
    d.ellipse([px(k + 1) - 6, by - 6, px(k + 1) + 6, by + 6], fill=(210, 40, 40))
    lbl = f"最高份 #{k+1}（第 {k+1}/{n} 份）"
    tx = min(px(k + 1) + 10, x1 - 190)
    d.text((tx, max(A_t + 4, by - 26)), lbl, fill=(190, 20, 20), font=font)

    # ================= 下面板: R/G/B 三通道锐度(诊断) =================
    allv = np.concatenate([pc[name][np.isfinite(pc[name])]
                           for name in ('R', 'G', 'B')])
    bv_lo, bv_hi = float(allv.min()), float(allv.max())
    bp = max(0.001, (bv_hi - bv_lo) * 0.12)
    bv_lo -= bp; bv_hi += bp

    for i in range(4):
        v = bv_lo + (bv_hi - bv_lo) * i / 3
        y = yof(v, bv_lo, bv_hi, B_t, B_b)
        d.line([(x0, y), (x1, y)], fill=(238, 238, 238))
        d.text((10, y - 8), f"{v:.4f}", fill=(130, 130, 130), font=font_xs)
    d.line([(x0, B_b), (x1, B_b)], fill=(120, 120, 120))
    d.line([(x0, B_t), (x0, B_b)], fill=(120, 120, 120))
    d.text((10, B_t - 28), "三通道锐度", fill=(60, 60, 60), font=font_s)

    colors = {'R': (214, 45, 45), 'G': (30, 150, 60), 'B': (40, 90, 220)}
    # 峰值标注 y 错位: G/B 常同峰, 错开避免文字重叠
    label_dy = {'R': -28, 'G': -46, 'B': -12}
    for name in ('R', 'G', 'B'):
        v = pc[name]
        cpts = [(px(i + 1), yof(v[i], bv_lo, bv_hi, B_t, B_b))
                for i in range(n) if np.isfinite(v[i])]
        if len(cpts) > 1:
            d.line(cpts, fill=colors[name], width=2)
        kk = peaks.get(name)
        if kk is not None and kk < n and np.isfinite(v[kk]):
            cx, cy = px(kk + 1), yof(v[kk], bv_lo, bv_hi, B_t, B_b)
            d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=colors[name])
            d.text((min(cx + 10, x1 - 130), cy + label_dy[name]),
                   f"{name} #{kk+1}", fill=colors[name], font=font_s)

    # ---------- 共用 X 轴 ----------
    step = max(1, n // 9)
    for i in range(0, n, step):
        x = px(i + 1)
        d.line([(x, B_b), (x, B_b + 5)], fill=(120, 120, 120))
        d.text((x - 8, B_b + 8), str(i + 1), fill=(120, 120, 120), font=font_xs)
    d.text(((x0 + x1) // 2 - 130, base_h - 26),
           f"份序号（#1 = 低端/不垫高 → #{n} = 高端/垫高侧）",
           fill=(60, 60, 60), font=font_s)

    # ---------- 底部: 固定结果展示(醒目红字横幅) ----------
    # 第1遍 = 给出第2遍区间建议; 第2遍 = 给出最终推荐垫高(0.1mm)
    banner = None
    if fine is not None:
        flo, fhi = fine
        banner = (f"第二遍精测建议：低端 {flo:.1f}mm / 高端 {fhi:.1f}mm"
                  f"（跨度 {fhi-flo:.1f}mm，{fine_note}）")
    elif final is not None:
        flo, fhi = final
        banner = (f"最终推荐垫高：{flo:.1f} – {fhi:.1f} mm"
                  f"（四脚统一垫到此区间，精度 0.1mm）")
    if banner is not None:
        d.rectangle([x0, base_h + 4, x1, H - 4],
                    fill=(255, 240, 240), outline=(205, 35, 35), width=2)
        bb = d.textbbox((0, 0), banner, font=font_big)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        cx = (x0 + x1 - tw) // 2
        cy = (base_h + H) // 2 - th // 2 - bb[1]
        d.text((cx, cy), banner, fill=(200, 15, 15), font=font_big)

    img.save(out_path)


def make_100pct_crop(S, k, idx_lo, idx_hi, out_path, quality=100):
    """7步 7: 回原图 FULL WIDTH 截取 11 份高度区间(100% 画质), 红框横贯全宽圈出最高份。
    装载约定: 图像顶部 = 低端, 故份 #1 = 图像顶部, 坐标直接裁即可。
    注意: 截的是原图「全宽 × 高度区间」, 不限于中心竖条。"""
    img = S["img_color"]
    full_W = img.shape[1]
    edges = S["edges"]
    y0 = int(round(edges[idx_lo]))
    y1 = int(round(edges[idx_hi + 1]))
    ky0 = int(round(edges[k]))
    ky1 = int(round(edges[k + 1]))
    crop = img[y0:y1, :].copy()          # 全宽, 仅高度区间
    ky0 -= y0
    ky1 -= y0
    thick = max(2, crop.shape[0] // 220)
    cv2.rectangle(crop, (0, ky0), (full_W - 1, ky1), (0, 0, 255), thick)
    cv2.putText(crop, f"最高份 #{k+1} (全宽)", (8, max(20, ky0 + 24)),
                cv2.FONT_HERSHEY_SIMPLEX, max(0.6, crop.shape[0] / 900),
                (0, 0, 255), max(2, thick // 2))
    ok = imwrite_safe(out_path, crop, quality=quality)
    return ok, (y0, y1)


# ============ 纵向色差 LoCA 峰值选择 ============
def pick_peak_index(v, tol=0.005):
    """与主流程一致的峰值选择: 归一化后 FLAT_TOL 平顶判定 + 中位索引。

    返回 (k, flat_count)。k=None 表示全为 NaN。
    tol 是归一化(0..1)后的容差, 不能直接用在原始 sharp(~0.12)上。
    """
    finite = np.isfinite(v)
    if not finite.any():
        return None, 0
    vv = np.where(finite, v, np.nan)
    mx, mn = np.nanmax(vv), np.nanmin(vv)
    span = mx - mn
    thr = mx - tol * span if span > 1e-12 else mx
    flat = np.where(finite & (v >= thr))[0]
    if len(flat) == 0:
        return None, 0
    return int(round(float(np.median(flat)))), len(flat)


# ===================== 主流程 =====================
def main():
    ap = argparse.ArgumentParser(
        description="片夹一端垫高 · 倾斜标靶长条扫描 焦平面扫描 (7步流水线)")
    ap.add_argument("scan", help="长条扫描单图, 或含多张的文件夹(取中位数)")
    ap.add_argument("--lift-mm", type=float, default=0.0,
                    help="片夹高的一端相对低的一端垫高的 mm 数(第1步询问项)")
    ap.add_argument("--strip-mm", type=float, default=0.0,
                    help="长条沿斜面方向的物理长度 mm(可选, 用于换算位置)")
    ap.add_argument("--low-lift-mm", type=float, default=None,
                    help="低端绝对垫高 mm；显式传入(即使为 0)=第二遍精测, "
                         "脚本输出最终推荐垫高 X–X+0.1mm；不传=第一遍粗测")
    ap.add_argument("--w-sharp", type=float, default=1.0,
                    help="综合分中 锐度 的权重(默认1.0)")
    ap.add_argument("--n", type=int, default=40, help="竖条均分份数(默认40)")
    ap.add_argument("--ksize", type=int, default=3)
    ap.add_argument("--strip", type=float, default=0.8,
                    help="中心竖条宽度: >=1 为像素(如 3500), 0-1 为图像宽度比例"
                         "(默认0.8 = 中央 80%% 区域)")
    ap.add_argument("--out", default=None, help="输出目录(默认脚本同目录)")
    args = ap.parse_args()

    out_dir = args.out or HERE
    os.makedirs(out_dir, exist_ok=True)

    files = list_images(args.scan)
    if not files:
        print(f"未找到图像: {args.scan}")
        sys.exit(1)

    # 多张则逐张测、取中位数(单张时自然只有一张)
    all_sharp, all_res, all_ne = [], [], []
    all_pc = {'R': [], 'G': [], 'B': []}
    S0 = None
    for f in files:
        S = build_strip(f, strip_w=args.strip, n_parts=args.n)
        if S is None:
            print(f"  ⚠ 跳过(无法读取): {os.path.basename(f)}")
            continue
        if S0 is None:
            S0 = S
        sh, pc_f, res, ne = measure_parts(S, ksize=args.ksize)
        all_sharp.append(sh); all_res.append(res); all_ne.append(ne)
        for name in ('R', 'G', 'B'):
            all_pc[name].append(pc_f[name])
    if S0 is None:
        print("所有图像均无法读取。")
        sys.exit(1)

    sharp = np.nanmedian(np.array(all_sharp), axis=0)
    res = np.nanmedian(np.array(all_res), axis=0)
    nedge = np.nanmedian(np.array(all_ne), axis=0)
    pc = {name: np.nanmedian(np.array(all_pc[name]), axis=0)
          for name in ('R', 'G', 'B')}
    n = len(sharp)

    # 综合分 = 灰度锐度(唯一评分量)
    comp, sN = comprehensive(sharp, w_sharp=args.w_sharp)
    # ---- 平顶处理: 平顶容差内用 sharp 找峰 + 取中位索引 ----
    # 平顶(如 2.0-2.75 段)时 nanargmax 取第一个最大值会定向偏低端且跨扫描跳变;
    # 故平顶区内改用 sharp 找峰, 并对等效峰取中位索引(单点居中, 不偏低端)。
    FLAT_TOL = 0.005                            # comp 平顶判定容差(0..1); 需实测 comp 曲线校准
    _comp_max = np.nanmax(comp)
    _flat = np.isfinite(comp) & (comp >= _comp_max - FLAT_TOL)
    if int(_flat.sum()) > 1:
        _sharp_flat = np.where(_flat, sharp, -np.inf)
        _sharp_max = np.nanmax(_sharp_flat)
        _peak = np.where(_flat & np.isfinite(sharp) & (sharp >= _sharp_max - 1e-12))[0]
        k = int(round(float(np.median(_peak))))   # 平顶区: sharp 取峰 + 中位索引
    else:
        k = int(np.nanargmax(comp))                 # 综合最高份
    flat_idx = np.where(_flat)[0]                   # 与最高分并列(容差内)的所有份
    idx_lo, idx_hi = clamp_window(k, WIN_HALF, n)   # 中心 ±WIN_HALF; 两端不足则截断
    trunc_top, trunc_bottom = window_truncated(k, WIN_HALF, n)

    # 用户指定: 最佳份序号 Y(1..n) → 距低端比例 frac_best = Y / n
    frac_best = (k + 1) / n if n else 0.0
    low_given = args.low_lift_mm is not None   # 显式传入=第2遍(即使值为0)
    low_val = args.low_lift_mm if low_given else 0.0
    phys = None
    if args.lift_mm > 0:
        phys = physical_height(frac_best, args.lift_mm, args.strip_mm, low_val)
    fine, final = None, None
    if phys is not None:
        if low_given:
            final = recommend_final_lift(phys['abs_best_height_mm'])
        else:
            # 仅当第一遍峰值恰贴最后一份(最高端)时向上外推, 其余一律居中
            fine = recommend_fine_window(phys['focal_plane_height_mm'],
                                         at_top_edge=(k == n - 1))

    # ---- 纵向色差 LoCA(主流程色差计算) ----
    peaks, Zs, flat_counts = {}, {}, {}
    for name in ('R', 'G', 'B'):
        kk, fc = pick_peak_index(pc[name])
        peaks[name], flat_counts[name] = kk, fc
        if kk is None or not args.lift_mm:
            Zs[name] = float('nan')
        else:
            base = low_val if low_given else 0.0
            Zs[name] = base + args.lift_mm * (kk + 1) / n
    loca_ok = args.lift_mm and all(z == z for z in Zs.values())

    # ---- 图表 + 回截 ----
    # 横幅附注: 峰值贴最高份=向上外探, 否则=峰值居中
    top_edge = (fine is not None and k == n - 1)
    fine_note = ("峰值贴顶·向上外探" if top_edge else "峰值居中")
    prof_path = os.path.join(out_dir, "tilt_sweep_profile.png")
    make_chart(sharp, pc, peaks, comp, k, idx_lo, idx_hi, n,
               prof_path, "倾斜标靶焦平面扫描 · 得分最高位置",
               fine=fine, final=final, fine_note=fine_note)
    crop_path = os.path.join(out_dir, "tilt_best_window.jpg")
    ok, (y0, y1) = make_100pct_crop(
        S0, k, idx_lo, idx_hi, crop_path, quality=100)

    # ---- 控制台 ----
    print(f"倾斜标靶焦平面扫描完成 (中心竖条 {S0['W']}px 宽 × {S0['H']}px 高, 均分 {n} 份)")
    print(f"  计分方式: 灰度锐度本身（三通道 LoCA 只作诊断, 不参与评分）")
    print(f"  得分最高份: #{k+1}/{n}  位置 = 长轴(从图像顶部/低端计) {frac_best*100:.1f}% 处")
    print(f"    sharp = {sharp[k]:.4f}")
    if len(flat_idx) > 1:
        print(f"    并列: 第 {flat_idx[0]+1}–{flat_idx[-1]+1} 份得分相同(容差内), "
              f"取中位 #{k+1}")
    if k == 0 or k == n - 1:
        print(f"    ⚠ 峰值贴在长条端点(#{k+1})：真实最佳位置可能在长条之外，"
              f"建议把垫高加大后重扫确认。")
    win_note = ""
    if trunc_top:
        win_note += "  [⚠ 靠近低端, 前5份不足已截断]"
    if trunc_bottom:
        win_note += "  [⚠ 靠近高端, 后5份不足已截断]"
    print(f"  回截窗口: 第 {idx_lo+1}–{idx_hi+1} 份 (共 {idx_hi-idx_lo+1} 份), "
          f"原图 y ≈ [{y0}, {y1}]px{win_note}")
    if phys:
        Zf = phys['focal_plane_height_mm']
        abs_best = phys['abs_best_height_mm']
        low = low_val
        print(f"  → 物理换算:")
        print(f"      焦平面高度 Zf = {Zf:.3f} mm"
              f"(相对低端, 本次最清晰处胶片离玻璃的高度)")
        if low_given:
            print(f"      第二遍精测 → 绝对最佳高度 = 低端垫高({low:.3f}) + Zf({Zf:.3f}) "
                  f"= {abs_best:.3f} mm")
            print(f"      若四个支脚统一垫高、使片夹整体平行于玻璃 → 绝对统一垫高 "
                  f"{abs_best:.3f} mm  (= 低端垫高 {low:.3f} + Zf {Zf:.3f})")
        else:
            print(f"      若四个支脚统一垫高、使片夹整体平行于玻璃 → 统一垫高 "
                  f"{phys['uniform_lift_all_feet_mm']:.3f} mm  (= X × Y/{n})")
        if fine is not None:
            flo, fhi = fine
            span2 = fhi - flo
            print(f"  ★ 第二遍精测建议（第一遍结果直接换算，无需心算）:")
            if k == n - 1:
                print(f"      峰值贴第一遍最高份(#{n})，真实最佳可能在实测范围之外 → "
                      f"区间从 Zf 起向上外探")
                print(f"      推荐区间 = {flo:.1f} – {fhi:.1f} mm"
                      f"（跨度 {span2:.1f}mm，起点=Zf={Zf:.3f}，向上覆盖 {span2:.1f}mm）")
            else:
                print(f"      推荐区间 = {flo:.1f} – {fhi:.1f} mm"
                      f"（跨度 {span2:.1f}mm，Zf={Zf:.3f} 居中；前后各覆盖 {span2/2:.2f}mm）")
            print(f"      装夹: 低端(A组)垫 {flo:.1f} mm，高端(B组)垫 {fhi:.1f} mm")
            print(f"      第二遍扫描后运行:")
            print(f"        python tilt_focus_sweep.py <第二遍长条图> "
                  f"--lift-mm {span2:.1f} --n 10 --low-lift-mm {flo:.1f}")
            print(f"      分辨率 = {span2:.1f}/10 = {span2/10:.2f} mm/份"
                  f"（想更细可 --n 20 → {span2/20:.2f} mm/份）")
            if k == n - 1:
                print(f"      ⚠ 第一遍峰值贴最高端，此区间已整段向上外探；"
                      f"若第二遍仍贴最高份需再加大跨度重扫")
        if final is not None:
            flo, fhi = final
            print(f"  ★★ 最终结果（第二遍精测，可直接照做）:")
            print(f"      四个支脚统一垫高 = {flo:.1f} – {fhi:.1f} mm"
                  f"（宽 0.1mm，绝对最佳 {abs_best:.3f} mm 落在区间内）")
            print(f"      图上底部红字同此区间；按此同步垫高 4 个支脚即可")
            if k == 0 or k == n - 1:
                print(f"      ⚠ 第二遍峰值贴端点，推荐值可能偏低；"
                      f"按第一遍流程重设区间重扫")
        print(f"      若保持倾斜、把最清晰段移到长条正中 → 低端调整 "
              f"{phys['tilt_adjust_to_center_best_mm']:.3f} mm (正=垫高, 负=降低)")
        if args.strip_mm > 0:
            print(f"      最佳点距低端 = {phys['best_pos_mm_from_low']:.2f} mm")
    else:
        print("  (未提供 --lift-mm, 跳过物理垫高换算)")
    # ---- 纵向色差 LoCA 控制台输出(主流程色差结果) ----
    print(f"\n  === 纵向色差 LoCA（R/G/B 三通道锐度峰值, 诊断不计分）===")
    for name in ('R', 'G', 'B'):
        kk = peaks[name]
        fc = flat_counts[name]
        if kk is None:
            print(f"    {name}: 无有效数据")
            continue
        tag = "  ⚠平顶不确定" if fc > n * 0.15 else ""
        ztxt = f"  Z={Zs[name]:.3f}mm" if loca_ok else ""
        print(f"    {name}: 峰值 #{kk+1}/{n}  sharp={pc[name][kk]:.4f}"
              f"{ztxt}  平顶={fc}份{tag}")
    if loca_ok:
        print(f"    LoCA: R−G={(Zs['R']-Zs['G'])*1000:+.0f}µm  "
              f"G−B={(Zs['G']-Zs['B'])*1000:+.0f}µm  "
              f"R−B={(Zs['R']-Zs['B'])*1000:+.0f}µm")
        print(f"    分辨率 = X/n = {args.lift_mm/n*1000:.0f}µm/份; "
              f"峰值差若 <1 份只可判为“小于分辨率”")
        if any(fc > n * 0.15 for fc in flat_counts.values()):
            print(f"    ⚠ 存在宽平顶通道: 峰值份是平顶中位, 不代表明确焦平面; "
                  f"该通道 LoCA 读数不可信")
    else:
        print(f"    (需 --lift-mm 才能把峰值份换算成高度差)")

    print(f"  已保存:")
    print(f"    {os.path.basename(prof_path)}  (曲线图: 上=灰度锐度, 下=R/G/B 三通道曲线)")
    print(f"    {os.path.basename(crop_path)}  (原图回截, 100% 画质, 全宽, 红框=最高份)")
    print(f"  (逐份 sharp/三通道/LoCA 与物理垫高量见上方控制台输出)")


if __name__ == "__main__":
    main()
