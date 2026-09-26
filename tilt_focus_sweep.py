#!/usr/bin/env python3
"""
片夹一端垫高 · 倾斜标靶长条扫描 焦平面扫描 (tilt focus sweep)
==============================================================
场景: 把 USAF 1951 标靶菲林夹在片夹里, 一端垫高、一端不垫 → 标靶相对扫描仪
玻璃板呈一个斜面。扫描后得到一张"长条图像": 沿斜面方向(长条长轴)不同位置
离焦程度不同, 其中某一段恰好落在扫描仪最佳焦平面 → 该段锐度最高。

本工具**复用 focus_compare.py 的 `calculate_sharpness` + `chromatic_aberration`**
(即 a/ 标靶法的 sharp / CA 度量), 严格按以下 7 步处理单张长条扫描:

  1. 传入单张标靶 jpg, 并询问"片夹一端垫高了多少 mm"(--lift-mm)
  2. crop_center_strip: 从中轴裁 3500px 宽中心竖条(只锁标靶主体, 排除画幅差异)
  3. 竖条按高度均分成 n 份(默认45), 每份 3500 × (总高/n)
  4. 逐份 calculate_sharpness → sharp(细密黑白精度锐度)
  5. 逐份 chromatic_aberration → CA(横向色差, 单位≈px, 越小越好)
  6. 锐度(sharp)与色差(CA)按推荐置信度比重(默认 0.7:0.3)合成综合锐度, 排名
    7. 取综合最高那一份, 连同序号前5/后5(共11份)的高度数据, 回原图截取这一段
     (100% 画质, 红框圈出最高份), 并算出"最佳锐度应垫高高度"
     注: 若综合最高份 Y 靠近长条两端、不满足"前5/后5"时, 不足的一侧直接截断
     (窗口随之变窄, 不越界)。例: n=45、最高份为第3份(Y=3) → 截取第1–8份(0-7)。

  物理垫高公式(用户指定): 设片夹高/低两端垫高差 = X(mm), 长条均分 n(默认45) 份,
  扫描得到综合锐度最高的份序号 = Y(1..n), 则
      → 四个支脚统一垫起高度 = X × Y / n
  (即: 焦平面高度 Zf = X × Y / n; 整片平行于玻璃即全部落入焦平面)

  两遍精测(推荐, 定位精度提升约 3×):
    第1遍(粗): --lift-mm 4 --n 45  → 得 Zf1(如 0.89mm), 分辨率 ≈ 4/45 ≈ 0.089 mm/份
    第2遍(精): 低端垫高 = Zf1-0.2, 高端垫高 = Zf1+0.2 (跨度 0.4mm, 中心=Zf1), 扫描后
              --lift-mm 0.4 --n 15 --low-lift-mm <Zf1-0.2>
              → 脚本直接输出 绝对最佳高度 = 低端垫高 + Zf2 (分辨率 ≈ 0.4/15 ≈ 0.027 mm/份)
    注意: 第2遍峰值应落中间段(第 4–12/15 份); 若贴边(第1或15份)说明第1遍估计偏,
          需重设中心(用新的 Zf 重算低端/高端垫高)再跑第2遍。

用法:
  python tilt_focus_sweep.py <长条扫描单图或文件夹> [--lift-mm 1.25]
      [--strip-mm <长条物理长度mm>] [--low-lift-mm 0.0] [--w-sharp 0.7] [--w-ca 0.3]
      [--n 45] [--ksize 3] [--strip 3500] [--out .]

  --lift-mm   片夹"高的一端"相对"低的一端"垫高的毫米数(高低差 X, 用于换算物理垫高量)
  --strip-mm  长条沿斜面方向的物理长度(mm)(用于把峰值位置换算成 mm)
  --low-lift-mm  低端的绝对垫高 mm; 第2遍精测时填入, 脚本直接输出绝对最佳高度(= 低端 + Zf)
  --w-sharp / --w-ca  综合锐度中 锐度:色差 的置信度比重(默认 0.7:0.3)

装载约定(固定): 标靶菲林垫付高的一端 = 菲林上序号小的一端; 扫描后图像
  顶部即 = 低端(不垫高那端)。故"份 #1"恒为图像顶部 = 低端, Y 直接从图像
  顶部数起, 无需区分扫描装入方向。

输出(默认写脚本同目录):
  tilt_sweep_profile.png      沿长条 n 份(默认45)的 sharp / 反相CA / 综合 曲线 + 11份窗口
  tilt_best_window.jpg        原图回截的 11 份区间(100% 画质, 全宽, 红框圈最高份)
  (逐份 sharp/CA/综合 + 排名 + 物理垫高量 直接打印在控制台, 不再落盘 json/预览图)
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
    calculate_sharpness, chromatic_aberration,
    crop_center_strip, imread_safe, list_images,
)


# ===================== 切片(7步 2/3) =====================
def build_strip(path, strip_w=3500, n_parts=45):
    """载入单图 → 裁 3500 宽中心竖条 → 均分 45 份。

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


def measure_parts(S, ksize=3):
    """7步 4/5: 逐份算 sharp + CA。"""
    edges = S["edges"]
    strip_gray = S["strip_gray"]
    strip_color = S["strip_color"]
    n = len(edges) - 1
    sharp, ca, ca_p90, res = [], [], [], []
    for i in range(n):
        y0, y1 = int(round(edges[i])), int(round(edges[i + 1]))
        if y1 - y0 < 4:
            sharp.append(float('nan')); ca.append(float('nan'))
            ca_p90.append(float('nan')); res.append(float('nan'))
            continue
        gp = strip_gray[y0:y1, :]
        cp = strip_color[y0:y1, :]
        try:
            sh, _n, _m, mask, _g, rl = calculate_sharpness(gp, ksize=ksize)
            cm, cp90 = chromatic_aberration(cp, mask, ksize=ksize)
            sharp.append(float(sh)); ca.append(float(cm))
            ca_p90.append(float(cp90)); res.append(float(rl))
        except Exception:
            sharp.append(float('nan')); ca.append(float('nan'))
            ca_p90.append(float('nan')); res.append(float('nan'))
    return (np.array(sharp, float), np.array(ca, float),
            np.array(ca_p90, float), np.array(res, float))


# ===================== 综合锐度(7步 6) =====================
def comprehensive(sharp, ca, w_sharp=0.7, w_ca=0.3):
    """锐度(越高越好)与色差(越低越好)各自 min-max 归一化到 0..1 后加权合成。

    - sharp 是聚焦的直接、低噪声信号 → 权重高(默认 0.7)
    - CA 受镜头场曲/边缘效应影响、且逐份边缘数不均 → 噪声大、场相关 → 权重低(默认 0.3)
    返回 comp 数组(越大越好)。
    """
    s = sharp.astype(float).copy()
    c = ca.astype(float).copy()
    smin, smax = np.nanmin(s), np.nanmax(s)
    cmin, cmax = np.nanmin(c), np.nanmax(c)
    sN = np.full_like(s, 0.5)
    cN = np.full_like(c, 0.5)
    if smax - smin > 1e-12:
        sN = (s - smin) / (smax - smin)
    if cmax - cmin > 1e-12:
        cN = (cmax - c) / (cmax - cmin)   # 低色差 → 高 cN
    comp = w_sharp * sN + w_ca * cN
    return comp, sN, cN


# ===================== 回截窗口(7步 7 邻域) =====================
WIN_HALF = 5   # 最高份前/后各取 5 份 → 理想窗口 2*WIN_HALF+1 = 11 份

def clamp_window(k, half, n):
    """以综合最高份 k(0-based) 为中心, 取 [k-half, k+half] 共 2*half+1 份。

    靠近长条两端时, 不足的一侧直接截断(不越界), 窗口随之变窄:
      - k 偏小(靠近图像顶部=低端): idx_lo 夹到 0
      - k 偏大(靠近图像底部=高端): idx_hi 夹到 n-1
    例: n=45, 最高份 k=2(Y=3, 1-based) → (0, 7); k=3(Y=4) → (0, 8)。
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


def make_chart(centers, sharp, ca, comp, sN, cN, k, idx_lo, idx_hi,
               n, out_path, title):
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1000, 500
    pad_l, pad_r, pad_t, pad_b = 66, 24, 46, 52
    img = Image.new('RGB', (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 17)
        font_s = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 12)
    except Exception:
        font = ImageFont.load_default(); font_s = font
    x0, x1 = pad_l, W - pad_r
    y0, y1 = H - pad_b, pad_t

    def pxp(v):
        return x0 + v / max(1, n - 1) * (x1 - x0)   # 横轴=份序号 0..n-1
    def pyp(v):
        return y0 + v * (y1 - y0)                   # 纵轴=归一化 0..1

    # 11 份窗口阴影
    d.rectangle([pxp(idx_lo) - 6, y1, pxp(idx_hi) + 6, y0], fill=(208, 246, 208))
    # 三线
    def plot(arr, color, width=2):
        pts = [(pxp(i), pyp(arr[i])) for i in range(n)
               if np.isfinite(arr[i])]
        if len(pts) > 1:
            d.line(pts, fill=color, width=width)
    plot(sN, (60, 150, 200))        # sharp 归一化(蓝)
    plot(cN, (230, 150, 40))        # 反相CA 归一化(橙)
    plot(comp, (30, 30, 30), 3)     # 综合(黑粗)
    # 最佳份红点
    bp = pxp(k); by = pyp(comp[k])
    d.line([(bp, y0), (bp, y1)], fill=(220, 0, 0), width=1)
    d.ellipse([bp - 5, by - 5, bp + 5, by + 5], fill=(220, 0, 0))
    d.text((bp + 7, max(pad_t, by - 18)), f"最高份 #{k+1}",
           fill=(220, 0, 0), font=font_s)
    # 轴
    d.line([(x0, y0), (x1, y0)], fill=(0, 0, 0))
    d.line([(x0, y0), (x0, y1)], fill=(0, 0, 0))
    d.text(((x0 + x1) // 2 - 120, H - 20), f"份序号 (低端=#1 → 高端=#{n})",
           fill=(0, 0, 0), font=font_s)
    d.text((8, pad_t - 28), title, fill=(0, 0, 0), font=font)
    d.text((8, (y0 + y1) // 2 - 60), "归一化", fill=(80, 80, 80), font=font_s)
    # 图例
    d.text((x1 - 250, pad_t + 4), "■ 综合锐度(黑)", fill=(30, 30, 30), font=font_s)
    d.text((x1 - 250, pad_t + 22), "■ sharp归一(蓝)", fill=(60, 150, 200), font=font_s)
    d.text((x1 - 250, pad_t + 40), "■ 反相CA归一(橙)", fill=(230, 150, 40), font=font_s)
    img.save(out_path)


def make_100pct_crop(S, k, idx_lo, idx_hi, out_path, quality=100):
    """7步 7: 回原图 FULL WIDTH 截取 11 份高度区间(100% 画质), 红框横贯全宽圈出最高份。
    装载约定: 图像顶部 = 低端, 故份 #1 = 图像顶部, 坐标直接裁即可。
    注意: 截的是原图「全宽 × 高度区间」, 不限于中心 3500px 竖条。"""
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


# ===================== 主流程 =====================
def main():
    ap = argparse.ArgumentParser(
        description="片夹一端垫高 · 倾斜标靶长条扫描 焦平面扫描 (7步流水线)")
    ap.add_argument("scan", help="长条扫描单图, 或含多张的文件夹(取中位数)")
    ap.add_argument("--lift-mm", type=float, default=0.0,
                    help="片夹高的一端相对低的一端垫高的 mm 数(第1步询问项)")
    ap.add_argument("--strip-mm", type=float, default=0.0,
                    help="长条沿斜面方向的物理长度 mm(可选, 用于换算位置)")
    ap.add_argument("--low-lift-mm", type=float, default=0.0,
                    help="低端(不垫高那端)的绝对垫高 mm；第二遍精测时填入, "
                         "脚本将直接输出绝对最佳高度 = 低端垫高 + Zf")
    ap.add_argument("--w-sharp", type=float, default=0.7,
                    help="综合锐度中 锐度 的置信度比重(默认0.7)")
    ap.add_argument("--w-ca", type=float, default=0.3,
                    help="综合锐度中 色差 的置信度比重(默认0.3)")
    ap.add_argument("--n", type=int, default=45, help="竖条均分份数(默认45)")
    ap.add_argument("--ksize", type=int, default=3)
    ap.add_argument("--strip", type=int, default=3500,
                    help="中心竖条宽度(px, 默认3500)")
    ap.add_argument("--out", default=None, help="输出目录(默认脚本同目录)")
    args = ap.parse_args()

    out_dir = args.out or HERE
    os.makedirs(out_dir, exist_ok=True)

    files = list_images(args.scan)
    if not files:
        print(f"未找到图像: {args.scan}")
        sys.exit(1)

    # 多张则逐张测、取中位数(单张时自然只有一张)
    all_sharp, all_ca, all_ca90, all_res = [], [], [], []
    S0 = None
    for f in files:
        S = build_strip(f, strip_w=args.strip, n_parts=args.n)
        if S is None:
            print(f"  ⚠ 跳过(无法读取): {os.path.basename(f)}")
            continue
        if S0 is None:
            S0 = S
        sh, ca, ca90, res = measure_parts(S, ksize=args.ksize)
        all_sharp.append(sh); all_ca.append(ca); all_ca90.append(ca90); all_res.append(res)
    if S0 is None:
        print("所有图像均无法读取。")
        sys.exit(1)

    sharp = np.nanmedian(np.array(all_sharp), axis=0)
    ca = np.nanmedian(np.array(all_ca), axis=0)
    ca90 = np.nanmedian(np.array(all_ca90), axis=0)
    res = np.nanmedian(np.array(all_res), axis=0)
    centers = S0["centers"]
    n = len(sharp)

    comp, sN, cN = comprehensive(sharp, ca, w_sharp=args.w_sharp, w_ca=args.w_ca)
    k = int(np.nanargmax(comp))                 # 综合最高份
    idx_lo, idx_hi = clamp_window(k, WIN_HALF, n)   # 中心 ±WIN_HALF; 两端不足则截断
    trunc_top, trunc_bottom = window_truncated(k, WIN_HALF, n)

    # 用户指定: 最佳份序号 Y(1..n) → 距低端比例 frac_best = Y / n
    frac_best = (k + 1) / n if n else 0.0
    phys = None
    if args.lift_mm > 0:
        phys = physical_height(frac_best, args.lift_mm, args.strip_mm,
                                args.low_lift_mm)

    # ---- 图表 + 回截 ----
    prof_path = os.path.join(out_dir, "tilt_sweep_profile.png")
    make_chart(centers, sharp, ca, comp, sN, cN, k, idx_lo, idx_hi, n,
               prof_path, "倾斜标靶焦平面扫描 · 综合锐度排名")
    crop_path = os.path.join(out_dir, "tilt_best_window.jpg")
    ok, (y0, y1) = make_100pct_crop(
        S0, k, idx_lo, idx_hi, crop_path, quality=100)

    # ---- 控制台 ----
    print(f"倾斜标靶焦平面扫描完成 (竖条 {S0['H']}px 高, 均分 {n} 份, "
          f"综合比重 sharp:ca = {args.w_sharp:.2f}:{args.w_ca:.2f})")
    print(f"  综合最高份: #{k+1}/{n}  位置 = 长轴(从图像顶部/低端计) {frac_best*100:.1f}% 处")
    print(f"    sharp = {sharp[k]:.4f}   CA = {ca[k]:.3f}px   "
          f"res_limit = {res[k]:.1f}px(越小越清)")
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
        low = args.low_lift_mm
        print(f"  → 物理换算:")
        print(f"      焦平面高度 Zf = {Zf:.3f} mm"
              f"(相对低端, 本次最清晰处胶片离玻璃的高度)")
        if low > 0:
            print(f"      第二遍精测 → 绝对最佳高度 = 低端垫高({low:.3f}) + Zf({Zf:.3f}) "
                  f"= {abs_best:.3f} mm")
            print(f"      若四个支脚统一垫高、使片夹整体平行于玻璃 → 绝对统一垫高 "
                  f"{abs_best:.3f} mm  (= 低端垫高 {low:.3f} + Zf {Zf:.3f})")
        else:
            print(f"      若四个支脚统一垫高、使片夹整体平行于玻璃 → 统一垫高 "
                  f"{phys['uniform_lift_all_feet_mm']:.3f} mm  (= X × Y/{n})")
        print(f"      若保持倾斜、把最清晰段移到长条正中 → 低端调整 "
              f"{phys['tilt_adjust_to_center_best_mm']:.3f} mm (正=垫高, 负=降低)")
        if args.strip_mm > 0:
            print(f"      最佳点距低端 = {phys['best_pos_mm_from_low']:.2f} mm")
    else:
        print("  (未提供 --lift-mm, 跳过物理垫高换算)")
    print(f"  已保存:")
    print(f"    {os.path.basename(prof_path)}  (曲线图)")
    print(f"    {os.path.basename(crop_path)}  (原图回截, 100% 画质, 全宽, 红框=最高份)")
    print(f"  (逐份 sharp/CA/综合 与物理垫高量见上方控制台输出)")


if __name__ == "__main__":
    main()
