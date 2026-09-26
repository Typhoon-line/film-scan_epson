#!/usr/bin/env python3
"""
USAF 1951 分辨率极限自动测量
================================
从扫描标靶图反推 px/mm(物理像素尺度),在每张图各 group/element 的真实空间频率上
量调制深度,找出"极限可分辨"的 group/element,作为对比多张扫描镜头位的金标准。

为什么需要这个:
  原 focus_compare.py 的"sharp"是「细尺度能量比」,对 4-1/4-2 这种相邻元素的差异
  不敏感(粗→细的整段能量形状几乎一样)。USAF 1951 的 4-1 / 4-2 频率比仅 1.122 倍,
  只有直接到对应真实频率点读调制深度才能分辨。

原理:
  1) 滤掉"大块黑白整体"(σ=18 高斯低通减原图),只剩块内细线组(fine0)。
  2) 对 fine0 做 2D FFT,取 kx≈0 和 ky≈0 的窄条平均 → 1D 功率谱(cycles/px)。
  3) 找峰; USAF 1951 的元素频率是几何级数:
        f(g,e) = 2^{(g-1) + (e-1)/6}   (convention A, g=0 时 f=0.5 lp/mm)
     或
        f(g,e) = 2^{g + (e-1)/6}       (convention B, g=0 时 f=1 lp/mm)
     试两种,取拟合峰数多的一种; 由 (anchor 的 lpmm) / (anchor 的 px-freq) 得 px/mm。
  4) 对每个 group/element,在其真实 cycles/px 频率附近 ±4% 取平均功率,作为该频率
     的"调制深度"度量。多张图取中位数。
  5) "极限可分辨" = 调制深度高于某阈值(默认 0.3×最大)的最细 (g,e)。

用法:
  python usaf_resolve.py <a文件夹> [--strip 3500] [--save-prefix usaf_1]
"""
import os, sys, glob, json, argparse
import numpy as np
import cv2


# ===================== IO =====================
def imread_safe(path, flag=cv2.IMREAD_GRAYSCALE):
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, flag) if data.size else None


def orient_landscape(img):
    h, w = img.shape[:2]
    if h > w:
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    return img


def strip_center_square(img, side=3500):
    """从中心裁一个正方形(2D FFT 需要等长两轴)。side 取 min(side, h, w)。"""
    h, w = img.shape[:2]
    s = min(side, h, w)
    cx, cy = w // 2, h // 2
    half = s // 2
    return img[max(0, cy - half):min(h, cy + half),
               max(0, cx - half):min(w, cx + half)]


# ===================== 细线隔离与 1D 谱 =====================
SIGMA = 18


def get_fine0(gray, ksize=3):
    """滤掉"大块黑白整体"后只剩块内细线组(USAF 三横三竖组)。"""
    base = cv2.medianBlur(gray, ksize).astype(np.float64) if ksize > 1 \
        else gray.astype(np.float64)
    return base - cv2.GaussianBlur(base, (0, 0), SIGMA)


def line_spectrum(gray):
    """1D 功率谱: 对 fine0 做 2D FFT,取 kx≈0 和 ky≈0 附近 ±2 行/列平均。
    返回 (freqs, power),长度 W,频率范围 -0.5..0.5 cycles/px(已 fftshift)。"""
    f0 = get_fine0(gray)
    H, W = f0.shape
    F = np.fft.fftshift(np.fft.fft2(f0))
    mag = np.abs(F)
    half = 2
    h_axis = mag[H // 2 - half: H // 2 + half + 1, :].mean(axis=0)  # vs kx
    v_axis = mag[:, W // 2 - half: W // 2 + half + 1].mean(axis=1)  # vs ky
    spec = (h_axis + v_axis) / 2.0
    freqs = np.fft.fftshift(np.fft.fftfreq(W))
    return freqs, spec


# ===================== 找峰(不依赖 scipy) =====================
def find_peaks(y, min_dist=4, min_prom_frac=0.025):
    """返回峰在 y 中的下标(已排序、已 NMS)。"""
    cands = []
    for i in range(1, len(y) - 1):
        if y[i] > y[i - 1] and y[i] > y[i + 1]:
            cands.append(i)
    if not cands:
        return np.array([], dtype=int)
    cands = np.array(cands)
    ymin, ymax = float(y.min()), float(y.max())
    keep = []
    for i in cands:
        w = min_dist * 4
        lo = max(0, i - w)
        hi = min(len(y), i + w + 1)
        floor = float(y[lo:hi].min())
        if y[i] - floor >= min_prom_frac * (ymax - ymin + 1e-9):
            keep.append(i)
    if not keep:
        return np.array([], dtype=int)
    cands = np.array(sorted(keep))
    order = np.argsort(-y[cands])
    suppressed = np.zeros(len(cands), dtype=bool)
    keep2 = []
    for o in order:
        i = cands[o]
        if suppressed[o]:
            continue
        keep2.append(i)
        for j in range(len(cands)):
            if j == o:
                continue
            if abs(cands[j] - i) < min_dist:
                suppressed[j] = True
    return np.array(sorted(keep2))


# ===================== USAF 几何梯子 =====================
def expected_rels(conv='A', gmax=7):
    rels, labels = [], []
    for g in range(0, gmax + 1):
        for e in range(1, 7):
            if conv == 'A':
                f = 2.0 ** ((g - 1) + (e - 1) / 6.0)
            else:
                f = 2.0 ** (g + (e - 1) / 6.0)
            rels.append(f)
            labels.append((g, e))
    return np.array(rels), labels


def fit_ladder(peak_freqs, pxmm_range=(60, 350)):
    """约束版梯子拟合(修正量纲)。

    cycles/px 与 lp/mm 的换算: f_px = f_lpmm / pxmm(不是 ×)。
    最低峰可能对应 group 0/1/2 的 element 1(靶上最粗的可见组),轮流试这三个
    anchor × 两种 convention,要求 pxmm ∈ [60, 350] 物理合理,再贪心配其余峰,
    取配得最多的方案。
    """
    if len(peak_freqs) < 3:
        return None
    f_low = float(peak_freqs[0])
    relsA, labelsA = expected_rels('A')
    relsB, labelsB = expected_rels('B')
    candidates = []
    for conv, rels, labels in [('A', relsA, labelsA), ('B', relsB, labelsB)]:
        # 试把最低峰当 (g, 1) 的 element 1; g 试 0, 1, 2
        for g_anchor in (0, 1, 2):
            j_anchor = labels.index((g_anchor, 1))
            f_anchor = rels[j_anchor]  # lp/mm
            pxmm = f_anchor / f_low     # = lp/mm ÷ (cyc/px) → px/mm
            if not (pxmm_range[0] <= pxmm <= pxmm_range[1]):
                continue
            # 预测的各 (g,e) 在 cycles/px 处的频率
            pred_px = rels / pxmm
            mask = pred_px < 0.48
            pred_in = pred_px[mask]
            labels_in = [labels[i] for i in range(len(labels)) if mask[i]]
            if len(pred_in) == 0:
                continue
            order = np.argsort(peak_freqs)
            assigns = {}
            used = set()
            for k in order:
                f = float(peak_freqs[k])
                j = int(np.argmin(np.abs(np.log2(f) - np.log2(pred_in))))
                err = abs(np.log2(f) - np.log2(pred_in[j]))
                if err < 0.06 and j not in used:
                    assigns[k] = labels_in[j]
                    used.add(j)
            candidates.append((conv, g_anchor, pxmm, len(assigns), assigns))
    if not candidates:
        return None
    candidates.sort(key=lambda r: -r[3])
    return candidates[0]  # (conv, g_anchor, pxmm, n_assigned, assigns)


# ===================== 在某频率处取调制 =====================
def modulation_at(spec, freqs, f_target, bw=0.04):
    """在 [f_target·(1-bw), f_target·(1+bw)] 范围内的平均功率。"""
    m = (freqs >= f_target * (1 - bw)) & (freqs <= f_target * (1 + bw))
    if not m.any():
        return 0.0
    return float(spec[m].mean())


# ===================== 主流程 =====================
def analyze_scan(scan_path, strip=3500, ksize=3, gmax=7):
    files = sorted(glob.glob(os.path.join(scan_path, "*.jpg")))
    if not files:
        print(f"未找到图像: {scan_path}")
        return None
    per = []
    for f in files:
        img = imread_safe(f, 0)
        if img is None:
            continue
        img = orient_landscape(img)
        img = strip_center_square(img, side=strip)
        freqs, spec = line_spectrum(img)
        per.append((freqs, spec))
    if not per:
        print("全部图像无法读取")
        return None
    L = min(len(s) for _, s in per)
    specs = np.array([s[:L] for _, s in per])
    freqs = per[0][0][:L]
    med = np.median(specs, axis=0)
    pos = freqs > 0.003
    fp = freqs[pos]
    sp = med[pos]
    # 找峰
    pidx = find_peaks(sp, min_dist=4, min_prom_frac=0.03)
    pfreqs = fp[pidx]
    pvals = sp[pidx]
    # 试多种 anchor + 两种 convention
    res = fit_ladder(pfreqs)
    if res is None:
        print("梯子拟合失败(峰数太少或 px/mm 越界)")
        return None
    conv, g_anchor, pxmm, score, assigns = res
    return {
        'spec_freqs': fp, 'spec_power': sp,
        'peak_freqs': pfreqs, 'peak_vals': pvals, 'peak_assigns': assigns,
        'conv': conv, 'g_anchor': g_anchor, 'pxmm': float(pxmm),
        'n_peaks': len(pfreqs), 'n_assigned': score,
    }


def plot_spec(result, out_path, title=''):
    fp = result['spec_freqs']
    sp = result['spec_power']
    pxmm = result['pxmm']
    conv = result['conv']
    pfreqs = result['peak_freqs']
    pvals = result['peak_vals']
    assigns = result['peak_assigns']
    W, H = 1200, 460
    cv = np.full((H, W, 3), 255, np.uint8)
    x0, x1 = 50, W - 20
    y0, y1 = H - 50, 30
    fmax = 0.40
    idx = int(fmax / (0.5 / len(fp)))
    xs = fp[:idx]
    ys = sp[:idx]
    lo, hi = float(np.percentile(ys, 2)), float(np.percentile(ys, 99))
    for i in range(1, len(xs)):
        fx = x0 + (xs[i] - fp[0]) / (fmax - fp[0]) * (x1 - x0)
        fy0 = y1 + (ys[i - 1] - lo) / (hi - lo + 1e-9) * (y0 - y1)
        fy1 = y1 + (ys[i] - lo) / (hi - lo + 1e-9) * (y0 - y1)
        cv2.line(cv, (int(fx), int(fy0)), (int(fx), int(fy1)), (40, 40, 40), 1)
    # peaks
    for i, pfi in enumerate(pfreqs):
        if pfi > fmax:
            continue
        fx = int(x0 + (pfi - fp[0]) / (fmax - fp[0]) * (x1 - x0))
        fy = int(y1 + (pvals[i] - lo) / (hi - lo + 1e-9) * (y0 - y1))
        if i in assigns:
            g, e = assigns[i]
            cv2.circle(cv, (fx, fy), 5, (0, 0, 220), -1)
            cv2.putText(cv, f"{g}-{e}", (fx + 6, fy - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 220), 1)
        else:
            cv2.circle(cv, (fx, fy), 4, (120, 120, 120), -1)
    # expected USAF positions (green ticks)
    rels, labels = expected_rels(conv)
    for j, r in enumerate(rels):
        f_px = r / pxmm  # cycles/px = lp/mm / (px/mm)
        if 0 < f_px < fmax:
            fx = int(x0 + (f_px - fp[0]) / (fmax - fp[0]) * (x1 - x0))
            cv2.line(cv, (fx, y0 + 3), (fx, y0 + 12), (0, 170, 0), 1)
    cv2.rectangle(cv, (x0, y1), (x1, y0), (180, 180, 180), 1)
    cv2.putText(cv, f"{title}  conv={conv}  px/mm={pxmm:.1f}  ({pxmm*25.4:.0f}dpi)  "
                f"anchor g={result['g_anchor']}-1  assigned {result['n_assigned']}/{result['n_peaks']} peaks",
                (x0, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 60, 60), 1)
    for fv in [0.02, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35]:
        fx = int(x0 + (fv - fp[0]) / (fmax - fp[0]) * (x1 - x0))
        cv2.line(cv, (fx, y0), (fx, y0 + 4), (120, 120, 120), 1)
        cv2.putText(cv, f"{fv:.2f}", (fx - 12, H - 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (100, 100, 100), 1)
    cv2.imwrite(out_path, cv)


def modulation_table(result, gmax=7):
    """对每个 (g,e) 在其真实 cycles/px 频率处取调制深度(归一化到谱最大=1)。"""
    fp = result['spec_freqs']; sp = result['spec_power']
    pxmm = result['pxmm']; conv = result['conv']
    rels, labels = expected_rels(conv, gmax)
    ref = float(sp.max()) + 1e-9
    out = []
    for r, (g, e) in zip(rels, labels):
        f_px = r / pxmm  # cycles/px
        if f_px > 0.49 or f_px < 0.002:
            continue
        mod = modulation_at(sp, fp, f_px, bw=0.05) / ref
        out.append((g, e, r, f_px, mod))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scan", help="a/ 文件夹")
    ap.add_argument("--strip", type=int, default=3500)
    ap.add_argument("--save-prefix", default="usaf")
    args = ap.parse_args()
    r = analyze_scan(args.scan, strip=args.strip)
    if r is None:
        return
    print(f"conv={r['conv']}  g_anchor={r['g_anchor']}  px/mm={r['pxmm']:.2f}  "
          f"({r['pxmm']*25.4:.0f} dpi)  assigned {r['n_assigned']}/{r['n_peaks']} peaks")
    plot_spec(r, args.save_prefix + "_spec.jpg", title=os.path.basename(args.scan))
    print(f"saved {args.save_prefix}_spec.jpg")
    print()
    print("g  e   lp/mm    cycles/px   modulation")
    tbl = modulation_table(r)
    for g, e, lpmm, fpx, mod in tbl:
        bar = "#" * int(mod * 40)
        print(f"{g}  {e}  {lpmm:7.3f}  {fpx:.4f}    {mod:.4f}  {bar}")


if __name__ == "__main__":
    main()
