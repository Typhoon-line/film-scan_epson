#!/usr/bin/env python3
"""
菲林扫描镜头调位 · 子文件夹 a 清晰度/色散 对比工具
=================================================

分方向限量分辨率(用户核心要求): 看"极限可分辨的三横三竖组", 且竖/横分开报。
原理(实测校正过):
  - 用 2D FFT 的"水平频率切片"测三竖(竖条)调制, "垂直频率切片"测三横(横条)调制。
  - 关键校正: 这张 USAF 靶本身的横条能量就比竖条高 ~1.5 倍(连 group1 都如此),
    不能直接用绝对调制比方向。故每个方向各自用"自己的 group1(完美分辨基准)"
    归一化: m_norm(g) = m(g)/m(group1)。这样各方向都从 1.0 起算, 谁先掉到阈值
    以下谁就是该方向的限量分辨率。
  - GT-X800 实测原生分辨率 = 125 px/mm(=3175 dpi), 4 个目录 ladder 拟合均得此值,
    故 PXMM 默认 125, 可用 --pxmm 覆盖(换扫描仪/分辨率时)。
  - 物理结论: 扫描车沿一个方向运动 → 沿该方向存在运动模糊 → 平行于扫描方向的
    三横(需纵向分辨率)比三竖(需横向分辨率)差约 1 组, 即本工具测出的各向异性。

专测黑白标靶(USAF 1951 等)子文件夹 a。每调整一次扫描镜头位置 → 扫描一次
(同配置, 产出多张 jpg) → 运行本工具:

  从竖直中轴裁 3500px 宽中心竖条(覆盖标靶主体), 再测两项:
    - sharp  细密黑白精度锐度 = 看"大块黑白内部的细密黑白", 看"极限可分辨的
             三横三竖组"(不是看大块边沿梯度; 重影/离焦把细线糊成灰, 粗边还在,
             用旧梯度法会虚高, 此法正确压低)。
    - 色散   边缘彩色镶边 = 横向色差(CA), 量化成近似通道错位像素数(px)
  (旧版"彩噪σ"已废除: 它把色散与彩噪混在一起, 不再统计)

彩色底片的动态范围/偏色由独立脚本 color_compare.py 负责; 色差校正出图由
ca_correct.py 负责。本工具只做"测量 + 排名"。

用法:
  python focus_compare.py <a文件夹 或 单张jpg> [--label "镜头位置说明"]
                          [--save] [--history 路径]
"""

import sys
import os
import argparse
import json
import glob
import datetime

import cv2
import numpy as np


# 分方向限量分辨率需要 USAF 各组的标称空间频率(逐 convention 倍率)
from usaf_resolve import expected_rels


# GT-X800 实测原生分辨率: 125 px/mm (3175 dpi)。换扫描仪/分辨率时用 --pxmm 覆盖。
PXMM = 125.0


# ===================== 工具 =====================
def imread_safe(path, flag=cv2.IMREAD_GRAYSCALE):
    """Unicode 安全读取(OpenCV 在 Windows 下不支持中文路径, 用 np.fromfile 绕过)。"""
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, flag)


def orient_landscape(img):
    """统一为横构图(宽>高), 纯 90° 旋转不改锐度, 只为保证不同朝向图命中同一部位。"""
    h, w = (img.shape[:2] if img.ndim == 3 else img.shape)
    if h > w:
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    return img


def crop_center_strip(img, width=3500):
    """从竖直中轴(x=W/2)裁取 width 像素宽的中心竖条(全高)。

    目的: 只测标靶主体区域, 排除不同扫描之间"周围胶片留多留少 / 自制片夹
    截区域不同"造成的画幅差异, 让每次比较都锁定在标靶本身。
    width 大于图像宽时退化为全宽。兼容灰度(2D)与彩色(3D HWC)。
    """
    h, w = (img.shape[:2] if img.ndim == 3 else img.shape)
    half = min(width, w) // 2
    cx = w // 2
    x0 = max(0, cx - half)
    x1 = min(w, cx + half)
    return img[:, x0:x1]


# ===================== 黑白标靶锐度 =====================
# 只测"大块黑白内部的细密黑白": 先把整块(填充+粗边)滤掉, 再在块内细线组上
# 做多尺度带通, 找"极限可分辨"的那组(见 calculate_sharpness 说明)。
SIGMA_BLOCK = 18.0    # 大块黑白整体滤除尺度(周期 >> 2π·18≈113px 的块被去掉)
FINE_SIGMAS = (0.7, 1.0, 1.4, 2.0, 2.8, 4.0, 5.6, 8.0)


def fine_spectrum(denoised_base):
    """块内细密黑白在各细尺度上的调制深度比(曝光无关)。输入须为已去噪的浮点图。

    用 SIGMA_BLOCK 大尺度高斯把"大块黑白"整体(填充+粗边)减掉, 得到只剩块内细线组
    的 fine0; 然后在 fine0 上做多尺度带通, 每个尺度 s 的调制比
    ratio_s = RMS(band_s) / RMS(fine0)。该比值只描述细细节在不同空间频率上的分布
    形状, 与曝光/整体亮度无关。
    """
    coarse = cv2.GaussianBlur(denoised_base, (0, 0), SIGMA_BLOCK)
    fine0 = denoised_base - coarse             # 只剩块内细线组
    ref = float(np.sqrt(np.mean(fine0 ** 2))) + 1e-6
    out = []
    for sg in FINE_SIGMAS:
        b1 = cv2.GaussianBlur(fine0, (0, 0), sg)
        b2 = cv2.GaussianBlur(fine0, (0, 0), sg * 1.8)
        band = b1 - b2
        out.append(float(np.sqrt(np.mean(band ** 2))) / ref)
    return np.array(out, dtype=np.float64)


def calculate_sharpness(gray, ksize=3):
    """
    细密黑白精度锐度 —— 看标靶块内最细还能分辨的"三横三竖"组。

    用户纠正(关键):
      旧版取"全图梯度最高的前 20% 像素"做中位数, 但标靶上梯度最大的是
      *大块黑白边沿*(粗边), 细密线组梯度反而小 → 度量被粗边锐度主导; 而且
      g/c(边缘陡度)只反映"模糊宽度", 并不反映"能分辨多细"——只要模糊宽度相同,
      粗边和细线给的分一样。于是重影图(粗边在、细线被糊成灰)反而虚高。

    正确做法:
      1) base = medianBlur 去噪; 噪点强度单独记。
      2) coarse = GaussianBlur(base, 18px) 把"大块黑白"整体(填充+粗边)滤掉,
         fine0 = base - coarse → 只剩块内细密线组(黑条/白条过渡)。
      3) 在 fine0 上多尺度带通扫描, ratio_s = RMS(band_s)/RMS(fine0),
         该比值只描述细细节在不同空间频率上的分布形状, 与曝光/亮度无关。
      4) sharp = Σ(w_s·ratio_s)/Σw_s,  w_s = 1/sigma_s(越细权重越大)。
         → 越能分辨细线(细尺度能量占比越高)分越高; 重影/离焦把细尺度能量压没,
            分自然低。这才是"极限可分辨精度"。
      5) res_limit_px = 2π·σ_limit, σ_limit = 仍显著高于噪声底(>15%峰值)的
         最细尺度。数值越小 = 能分辨越细 = 越清晰。
    返回 (sharp, noise, n_measured, mask, g, res_limit_px):
      mask,g 仍基于全图真实边缘(供色散 CA 复用, 逻辑不变)。
    """
    base = cv2.medianBlur(gray, ksize).astype(np.float64) if ksize > 1 \
        else gray.astype(np.float64)
    noise = gray.astype(np.float64) - base
    noise_std = float(np.std(noise))

    # 全图真实边缘(供 CA 掩膜, 逻辑保持)
    gx = cv2.Sobel(base, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(base, cv2.CV_64F, 0, 1, ksize=3)
    g = np.sqrt(gx ** 2 + gy ** 2)
    blur = cv2.GaussianBlur(base, (0, 0), 15)
    c = np.abs(base - blur)
    g_thr = float(np.percentile(g, 80.0))
    mask = g > max(g_thr, 1e-3)
    mh, mw = mask.shape
    mg = max(2, int(min(mh, mw) * 0.03))
    mask[:mg, :] = False
    mask[-mg:, :] = False
    mask[:, :mg] = False
    mask[:, -mg:] = False

    # —— 细密黑白精度锐度 ——
    ratios = fine_spectrum(base)
    w = 1.0 / np.array(FINE_SIGMAS, dtype=np.float64)
    sharp = float(np.sum(w * ratios) / np.sum(w))

    mx = float(ratios.max())
    above = [sg for sg, r in zip(FINE_SIGMAS, ratios) if r > 0.15 * mx]
    sigma_limit = min(above) if above else FINE_SIGMAS[-1]
    res_limit_px = float(2 * np.pi * sigma_limit)

    return sharp, noise_std, int(mask.sum()), mask, g, res_limit_px


def chromatic_aberration(bgr, mask, ksize=3):
    """
    量化标靶边缘的彩色镶边 = 横向色差(lateral chromatic aberration, CA)。

    原理: 色差使 R/G/B 三通道在同一物理边缘上亚像素错位 → 边缘处出现彩色残差
          (中性黑/白边本应 R≈G≈B, 错位后 R-G/B-G≠0, 即肉眼看到的"彩虹镶边")。

    关键修正(实测发现): 归一化必须用"真·梯度"——np.gradient 的单位是
          强度/像素, 无放大系数; 而 cv2.Sobel 的核带约 4× 放大, 会把色散
          数值系统性压低约 4 倍(0.26→1.0px 这种假小值)。现改用 np.gradient。

    做法:
      1) 色度幅度 chroma = √( (R-G)² + (B-G)² )。中性黑/白区本应≈0,
         仅边缘错位处出现凸起(与边缘严格对应)。不做中值平滑(会抹掉色散凸起)。
      2) 真·梯度 g_true = |∇(灰度)|(np.gradient, 单位=强度/像素)。
      3) ca = chroma / g_true。边缘处 chroma≈Δ·g_true (Δ=通道错位px),
         故 ca≈Δ → 单位=通道错位像素数(px), 越大色差越重。
      4) 只在真实边缘(mask)上取中位数与 p90。
    返回 (ca_median, ca_p90):
      ca_median 代表"肉眼 100% 可见的典型镶边"(如 1~2px);
      ca_p90    代表"最严重的重灾边"(对应 200% 放大明显可见的镶边)。
    """
    bgr = bgr.astype(np.float64)
    r = bgr[:, :, 2]
    gch = bgr[:, :, 1]
    b = bgr[:, :, 0]
    rg = r - gch
    bg = b - gch
    chroma = np.sqrt(rg ** 2 + bg ** 2)
    # 真·梯度(单位: 强度/像素)
    gray = 0.299 * r + 0.587 * gch + 0.114 * b
    gy, gx = np.gradient(gray)
    g_true = np.sqrt(gx ** 2 + gy ** 2)
    ca = chroma / (g_true + 1e-6)
    vals = ca[mask] if mask.sum() > 200 else ca
    return float(np.median(vals)), float(np.percentile(vals, 90))


# ===================== 分方向限量分辨率(三竖 / 三横) =====================
def limiting_resolution(path, pxmm=PXMM, conv='A', gmax=7,
                        target_side=2200, thresh=0.30):
    """分方向限量分辨率: 竖条(三竖)与横条(三横)各算一组 USAF 调制深度,
    各自用本方向 group1(完美分辨基准)归一化, 返回限量组号与 group5-2 归一化值。

    关键修正(实测发现 group 号虚高 bug):
      旧版把裁方后 downscale 的因子 s 乘进 px/mm(写成 pxmm*s), 且 target 缩到 1200,
      导致真实 px/mm 被放大 ~3 倍, 整条 USAF 梯子被压进低频 bin, 每个组都"有能量"
      -> 限量算到 group 7+(假高)。正确: 降采样后每毫米像素数下降, 应为 pxmm/ds;
      且只缩到 ~2200px, 让 group5~6 仍落在奈奎斯特内(可见)。

    返回 dict:
      lim_v / lim_h : 三竖 / 三横 的限量组号(浮点, g+(e-1)/6; 越大越细=越清晰)
      g5v / g5h     : 三竖 / 三横 在 group5-2 的归一化调制(group1=1.0; 越大越清晰)
      nv / nh       : 全组归一化调制字典(供调试/出图)
    """
    img = imread_safe(path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    img = orient_landscape(img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    gray = crop_center_strip(gray, width=3500)
    h, w = gray.shape
    side = min(h, w)
    sy, sx = (h - side) // 2, (w - side) // 2
    sq = gray[sy:sy + side, sx:sx + side].astype(np.float64)
    ds = max(1.0, side / target_side)        # 降采样因子(>=1)
    if ds > 1.0:
        sq = cv2.resize(sq, (int(round(side / ds)), int(round(side / ds))))
    pxmm_used = pxmm / ds                    # 降采样后每毫米像素数下降(原 125 -> 约 78)
    sq -= sq.mean()
    F = np.fft.fftshift(np.fft.fft2(sq))
    mag = np.abs(F)
    hh, ww = mag.shape
    yy, xx = np.mgrid[0:hh, 0:ww]
    cy, cx = hh / 2, ww / 2
    kx = xx - cx
    ky = yy - cy
    r = np.sqrt(kx ** 2 + ky ** 2)
    rmax = min(cy, cx) - 2
    freq = r / rmax * 0.5                    # cycles/pixel
    angle = np.arctan2(ky, kx)
    # 竖条(三竖): 沿 x 方向有明暗变化 -> 水平频率轴(ky≈0); 横条(三横): 垂直频率轴
    band_v = (np.abs(angle) < np.pi / 8) | (np.abs(np.abs(angle) - np.pi) < np.pi / 8)
    band_h = (np.abs(angle - np.pi / 2) < np.pi / 8) | (np.abs(angle + np.pi / 2) < np.pi / 8)
    nb = 800
    bins = np.linspace(0, 0.5, nb + 1)
    ring = (freq > 0.0015) & (freq < 0.006)  # 低频频域参考(低于 group1, 不受 ladder 污染)
    ref = float(mag[ring].mean()) if ring.sum() > 0 else 1.0

    def prof(band):
        p = np.zeros(nb)
        for i in range(nb):
            m = (freq >= bins[i]) & (freq < bins[i + 1]) & band
            if m.sum() > 0:
                p[i] = mag[m].mean()
        return p

    pv = prof(band_v)
    ph = prof(band_h)
    rels, labels = expected_rels(conv, gmax)
    mv, mh = {}, {}
    for g, e in labels:
        f = rels[labels.index((g, e))] / pxmm_used
        if f > 0.49 or f <= 0:
            continue
        idx = int(round(f / 0.5 * nb))
        lo2, hi2 = max(0, idx - 2), idx + 3
        mv[(g, e)] = float(np.sqrt(max(pv[lo2:hi2].mean() / ref, 0))) if ref > 0 else 0
        mh[(g, e)] = float(np.sqrt(max(ph[lo2:hi2].mean() / ref, 0))) if ref > 0 else 0
    v1 = mv.get((1, 1), 0)
    h1 = mh.get((1, 1), 0)
    nv = {k: (v / v1 if v1 > 0 else 0) for k, v in mv.items()}
    nh = {k: (v / h1 if h1 > 0 else 0) for k, v in mh.items()}
    lim_v = lim_h = 0.0
    for g, e in labels:
        if nv.get((g, e), 0) > thresh:
            lim_v = max(lim_v, g + (e - 1) / 6.0)
        if nh.get((g, e), 0) > thresh:
            lim_h = max(lim_h, g + (e - 1) / 6.0)
    return {"lim_v": lim_v, "lim_h": lim_h,
            "g5v": nv.get((5, 2), 0), "g5h": nh.get((5, 2), 0),
            "nv": nv, "nh": nh}


# ===================== 单图分析 =====================
def analyze_image(path, ksize=3, strip=3500, pxmm=PXMM):
    """分析单张标靶图: 统一先裁取竖直中轴 strip 像素宽的中心竖条, 再算 sharp+CA+分方向限量。"""
    img = imread_safe(path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    img = orient_landscape(img)
    img = crop_center_strip(img, width=strip)
    used_w = img.shape[1]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sharp, _n, n, mask, g, res_limit = calculate_sharpness(gray, ksize=ksize)
    ca, ca_p90 = chromatic_aberration(img, mask, ksize=ksize)  # 色散(CA), 单位≈px
    lr = limiting_resolution(path, pxmm=pxmm)  # 分方向限量分辨率(三竖/三横)
    lim_v = lim_h = g5v = g5h = None
    if lr is not None:
        lim_v, lim_h = lr["lim_v"], lr["lim_h"]
        g5v, g5h = lr["g5v"], lr["g5h"]
    return {
        "file": os.path.basename(path),
        "mode": "sharp",
        "score": sharp,          # 排名依据 = sharp
        "sharp": sharp,
        "res_limit_px": res_limit,  # 极限可分辨周期(px), 越小越清晰
        "lim_v": lim_v,          # 三竖(竖条)限量组号, 越大越细=越清晰
        "lim_h": lim_h,          # 三横(横条)限量组号
        "g5v": g5v,              # 三竖 在 group5-2 的归一化调制(group1=1.0)
        "g5h": g5h,              # 三横 在 group5-2 的归一化调制
        "ca": ca,                # 色散指数中位数(≈通道错位px, 典型可见镶边)
        "ca_p90": ca_p90,        # 色散 p90(最重灾边)
        "n_measured": n,
        "strip_w": used_w,
    }


# ===================== 本次扫描汇总 =====================
def list_images(scan_path):
    if os.path.isfile(scan_path):
        return [scan_path]
    raw = (glob.glob(os.path.join(scan_path, "*.jpg"))
           + glob.glob(os.path.join(scan_path, "*.jpeg"))
           + glob.glob(os.path.join(scan_path, "*.png")))
    seen, files = set(), []
    for p in sorted(raw):
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            files.append(p)
    return files


def analyze_scan(scan_path, ksize=3, strip=3500, pxmm=PXMM):
    files = list_images(scan_path)
    if not files:
        print(f"未找到图像: {scan_path}")
        sys.exit(1)

    results = []
    for f in files:
        r = analyze_image(f, ksize=ksize, strip=strip, pxmm=pxmm)
        if r is None:
            print(f"  ⚠ 跳过(无法读取): {os.path.basename(f)}")
            continue
        results.append(r)

    if not results:
        print("所有图像均无法读取, 请检查扫描文件。")
        sys.exit(1)

    scores = [r["score"] for r in results]
    scan_score = float(np.median(scores))  # 用中位数抗离群帧

    cas = float(np.median([r["ca"] for r in results]))
    ca_p90s = float(np.median([r["ca_p90"] for r in results]))
    res_limits = float(np.median([r["res_limit_px"] for r in results]))
    lim_vs = [r["lim_v"] for r in results if r["lim_v"] is not None]
    lim_hs = [r["lim_h"] for r in results if r["lim_h"] is not None]
    g5vs = [r["g5v"] for r in results if r["g5v"] is not None]
    g5hs = [r["g5h"] for r in results if r["g5h"] is not None]
    best_img = max(results, key=lambda r: r["sharp"])
    aux = {"ca": cas, "ca_p90": ca_p90s, "res_limit_px": res_limits}
    if lim_vs:
        aux["lim_v"] = float(np.median(lim_vs))
    if lim_hs:
        aux["lim_h"] = float(np.median(lim_hs))
    if g5vs:
        aux["g5v"] = float(np.median(g5vs))
    if g5hs:
        aux["g5h"] = float(np.median(g5hs))

    return results, scan_score, aux, best_img


# ===================== 历史 =====================
def load_history(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {"scans": []}


def save_history(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


# ===================== 可视化(--save) =====================
def save_sharp_vis(path, out_path, max_side=1200, ksize=3, strip=3500):
    """导出实测区域红膜图: 红=本工具实际参与计分的块内细密黑白区域(中心竖条内)。"""
    img = imread_safe(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return False
    img = orient_landscape(img)
    img = crop_center_strip(img, width=strip)
    base = cv2.medianBlur(img, ksize).astype(np.float64) if ksize > 1 \
        else img.astype(np.float64)
    coarse = cv2.GaussianBlur(base, (0, 0), SIGMA_BLOCK)
    fine0 = np.abs(base - coarse)
    mask = fine0 > np.percentile(fine0, 80.0)   # 只红膜"块内细密区域"
    h, w = img.shape
    scale = min(1.0, max_side / max(h, w))
    if scale < 1.0:
        small = cv2.resize(img, (int(w * scale), int(h * scale)))
        msmall = cv2.resize((mask * 255).astype(np.uint8),
                            (int(w * scale), int(h * scale)))
    else:
        small = img
        msmall = (mask * 255).astype(np.uint8)
    base_col = cv2.cvtColor(small.astype(np.uint8), cv2.COLOR_GRAY2BGR)
    red = np.zeros_like(base_col)
    red[:, :] = (0, 0, 255)
    m3 = (msmall > 127)[:, :, None]
    out = np.where(m3, cv2.addWeighted(base_col, 0.55, red, 0.45, 0), base_col)
    cv2.imwrite(out_path, out)
    return True


def save_spectrum(path, out_path, max_side=900, ksize=3, strip=3500):
    """导出细密黑白各尺度调制深度柱状图: 横轴粗→细, 红线=极限可分辨周期。"""
    img = imread_safe(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return False
    img = orient_landscape(img)
    img = crop_center_strip(img, width=strip)
    base = cv2.medianBlur(img, ksize).astype(np.float64) if ksize > 1 \
        else img.astype(np.float64)
    ratios = fine_spectrum(base)

    W, H = 900, 340
    canvas = np.full((H, W, 3), 255, np.uint8)
    n = len(FINE_SIGMAS)
    bw = W // (n + 1)
    mx = float(ratios.max()) if ratios.max() > 0 else 1.0
    for i, sg in enumerate(FINE_SIGMAS):
        x = int((i + 0.5) * bw)
        bar_h = int(ratios[i] / mx * (H - 50))
        cv2.rectangle(canvas, (x - bw // 3, H - 30),
                      (x + bw // 3, H - 30 - bar_h), (70, 70, 70), -1)
        cv2.putText(canvas, f"{2*np.pi*sg:.0f}", (x - 12, H - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 120, 120), 1)
    cv2.putText(canvas, "周期(px) → 粗", (6, H - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (120, 120, 120), 1)

    above = [sg for sg, r in zip(FINE_SIGMAS, ratios) if r > 0.15 * mx]
    if above:
        idx = FINE_SIGMAS.index(min(above))
        x = int((idx + 0.5) * bw)
        cv2.line(canvas, (x, 8), (x, H - 30), (0, 0, 255), 2)
        cv2.putText(canvas, f"limit~{2*np.pi*min(above):.1f}px",
                    (max(4, x - 70), 22), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (0, 0, 255), 1)
    cv2.imwrite(out_path, canvas)
    return True


# ===================== 主流程 =====================
def main():
    ap = argparse.ArgumentParser(
        description="菲林扫描镜头调位 · 子文件夹a 清晰度/色散 对比工具")
    ap.add_argument("scan", help="单次扫描的 a 文件夹, 或单张 jpg")
    ap.add_argument("--label", default=None, help="本次镜头位置说明(可选)")
    ap.add_argument("--history", default="focus_history.json",
                    help="历史记录文件路径, 默认 focus_history.json")
    ap.add_argument("--save", action="store_true",
                    help="导出可视化: measured_region.jpg(块内细密区域) + "
                         "fine_spectrum.jpg(各尺度调制深度, 红线=极限可分辨周期)")
    ap.add_argument("--ksize", type=int, default=3,
                    help="锐度去噪核大小, 默认3(彩噪/颗粒重可试5)")
    ap.add_argument("--strip", type=int, default=3500,
                    help="测量时从竖直中轴裁取的中心竖条宽度(像素), 默认3500; "
                         "只测标靶主体, 排除不同扫描间周围胶片留多留少的差异")
    ap.add_argument("--pxmm", type=float, default=PXMM,
                    help="标靶物理分辨率(px/mm), 默认125(=GT-X800 实测原生 3175dpi); "
                         "换扫描仪/分辨率时用此覆盖")
    args = ap.parse_args()

    print(f"分析(子文件夹a, 标靶): {args.scan}")
    print(f"(去噪核 ksize={args.ksize}, 中心竖条宽={args.strip}px, px/mm={args.pxmm})\n")

    results, scan_score, aux, best_img = analyze_scan(
        args.scan, ksize=args.ksize, strip=args.strip, pxmm=args.pxmm)

    # ---- 打印明细 ----
    print(f"{'图像':<24}{'sharp':<10}{'三竖限量':<10}{'三横限量':<10}"
          f"{'g5三竖':<9}{'g5三横':<9}{'色散中位':<10}{'色散p90'}")
    print("-" * 100)
    for r in results:
        lv = f"{r['lim_v']:.1f}" if r["lim_v"] is not None else "—"
        lh = f"{r['lim_h']:.1f}" if r["lim_h"] is not None else "—"
        g5v = f"{r['g5v']:.2f}" if r["g5v"] is not None else "—"
        g5h = f"{r['g5h']:.2f}" if r["g5h"] is not None else "—"
        print(f"{r['file']:<24}{r['sharp']:<10.4f}{lv:<10}{lh:<10}"
              f"{g5v:<9}{g5h:<9}{r['ca']:<10.3f}{r['ca_p90']:<10.3f}")
    print("-" * 100)
    print(f"本次扫描汇总 sharp 分数(各图中位数): {scan_score:.6f}  "
          f"(共 {len(results)} 张有效图, 中心竖条宽≈{results[0]['strip_w']}px)\n"
          f"  分方向限量分辨率(组号, 越大=能分辨越细):\n"
          f"    三竖(竖条) ≈ group {aux.get('lim_v',0):.1f}   "
          f"group5-2 归一化调制={aux.get('g5v',0):.2f}\n"
          f"    三横(横条) ≈ group {aux.get('lim_h',0):.1f}   "
          f"group5-2 归一化调制={aux.get('g5h',0):.2f}\n"
          f"  → 各向异性: 三横比三竖差约 {max(0,aux.get('lim_v',0)-aux.get('lim_h',0)):.1f} 组 "
          f"(扫描车运动模糊所致)\n"
          f"  色散: 中位数≈{aux['ca']:.3f}px   p90≈{aux['ca_p90']:.3f}px\n"
          f"  极限可分辨周期≈{aux['res_limit_px']:.1f}px (越小=越清晰)\n")
    print("(sharp = 块内细密黑白精度锐度; 三竖/三横限量 = 分方向极限可分辨组; "
          "色散 = 边缘彩色镶边, 越大色差越重)\n")

    # ---- 写历史 ----
    hist = load_history(args.history)
    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    label = args.label or (
        os.path.basename(os.path.normpath(args.scan)) + " @ " + ts)
    entry = {"ts": ts, "label": label, "dir": os.path.abspath(args.scan),
             "mode": "sharp", "score": round(scan_score, 6),
             "n_images": len(results),
             "sharp": round(scan_score, 6),
             "res_limit_px": round(aux["res_limit_px"], 2),
             "lim_v": round(aux["lim_v"], 2) if "lim_v" in aux else None,
             "lim_h": round(aux["lim_h"], 2) if "lim_h" in aux else None,
             "g5v": round(aux["g5v"], 3) if "g5v" in aux else None,
             "g5h": round(aux["g5h"], 3) if "g5h" in aux else None,
             "ca": round(aux["ca"], 3),
             "ca_p90": round(aux["ca_p90"], 3),
             "per_image": [{"file": r["file"], "sharp": round(r["sharp"], 4),
                            "res_limit_px": round(r["res_limit_px"], 2),
                            "lim_v": round(r["lim_v"], 2) if r["lim_v"] is not None else None,
                            "lim_h": round(r["lim_h"], 2) if r["lim_h"] is not None else None,
                            "g5v": round(r["g5v"], 3) if r["g5v"] is not None else None,
                            "g5h": round(r["g5h"], 3) if r["g5h"] is not None else None,
                            "ca": round(r["ca"], 3),
                            "ca_p90": round(r["ca_p90"], 3)}
                           for r in results]}
    hist["scans"].append(entry)
    save_history(args.history, hist)

    # ---- 历史排名(全部为 sharp 模式) ----
    same = [s for s in hist["scans"] if s.get("mode") == "sharp"]
    same.sort(key=lambda s: s["score"], reverse=True)
    rank = same.index(entry) + 1
    total = len(same)

    print("=" * 78)
    print(f"历史对比(共 {total} 次扫描):")
    print("-" * 78)
    for i, s in enumerate(same):
        mark = "← 本次" if s is entry else ""
        if "res_limit_px" in s:
            extra = (f"  sharp={s['sharp']:.4f} 极限周期={s['res_limit_px']:.1f}px"
                     f" 色散={s['ca']:.3f}px(p90 {s.get('ca_p90',0):.2f})")
        else:
            extra = f"  sharp={s['sharp']:.4f} (旧:彩噪σ={s.get('noise',0):.2f})"
        print(f"  #{i+1:<3} 分={s['score']:.4f}  {s['label']:<30}{extra}{mark}")
    print("-" * 78)

    if rank == 1:
        print(f"🎉 本次为历史最高!  (排名 1/{total})")
    else:
        best = same[0]
        bv = best["score"]
        diff = (bv - scan_score) / bv * 100 if bv else 0.0
        print(f"⬇️ 未超过历史最佳: 「{best['label']}」 score={bv:.4f}")
        print(f"    本次低 {diff:.1f}%  (排名 {rank}/{total})")
    print("=" * 78)

    # ---- 可视化 ----
    if args.save:
        if os.path.isfile(args.scan):
            src = args.scan
        else:
            src = os.path.join(args.scan, best_img["file"])
        if save_sharp_vis(src, "measured_region.jpg", ksize=args.ksize,
                          strip=args.strip):
            print(f"\n已保存 measured_region.jpg (红=本次实测的块内细密黑白区域, "
                  f"来自 {best_img['file']})")
        if save_spectrum(src, "fine_spectrum.jpg", ksize=args.ksize,
                         strip=args.strip):
            print(f"已保存 fine_spectrum.jpg (细密黑白各尺度调制深度, "
                  f"红线=极限可分辨周期, 来自 {best_img['file']})")


if __name__ == "__main__":
    main()
