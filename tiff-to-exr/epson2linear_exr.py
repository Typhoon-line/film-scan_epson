#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
epson2linear_exr.py — Epson Scan 48bit TIFF → scene-linear 32bit float EXR

【为什么是 gamma=1.8】
Epson Scan "无色彩校正"导出的 48bit TIFF 仍带 gamma 编码（文件里没有 ICC /
gamma 标签，编码烘在像素值里）。用一张 Epson 48bit TIFF 与同帧 VueScan Raw 对照实测：
SIFT+RANSAC 求几何变换 → 采样点投回两张原图直接取 5x5 均值（零插值）
→ 数十万有效点逐通道 log-log 拟合。扫描 gamma 找斜率=1 的点：
    g=1.75 → 0.965,  g=1.80 → 0.992,  g=1.85 → 1.020,  g=2.20 → 1.213
⇒ 被测机器（V700 + 该驱动版本）无色彩校正输出 gamma ≈ 1.8（不是默认假设的 2.2）。
也印证了第三方资料 "Epson Scan raw output apparently gamma 1.8"。
⚠ gamma 是机器/驱动相关的实测值，换机器或驱动版本必须重新标定，方法见
   references/gamma标定方法.md。

【输入要求】
Epson Scan 专业模式：正片模式 / 无色彩校正 / 48bit RGB / 无压缩 /
USM=无 / 自动曝光关 / 不嵌 ICC。本脚本只接受未压缩 48bit RGB TIFF
（Photometric=RGB，StripOffsets 处连续存放；Epson Scan 输出符合）。

【输出】
  <stem>_linear_g18.exr          32bit float / ZIP / R,G,B 三通道 → 给达芬奇/NamiColor
  <stem>_linear_g18_preview.png  8bit sRGB 回编码 → 只给人眼预览（--preview）
  <stem>_linear_g18_float32.tif  32bit float TIFF → 本地计算备份（--float-tiff）

【达芬奇 / NamiColor 设置】
1. 拖入 EXR；第一个节点挂 DCTL OFX → NamiColor（需 Resolve Studio v17+）
2. 输入色彩空间下拉选 bypass（数据是 Epson 扫描仪 RGB 未校色空间，不是 AdobeRGB）
3. 处理模式 Negatives / Log-to-Log，Input Gain 调到数值落进 0–1
4. 线性化已在本脚本完成，NamiColor 不再做 gamma 修正
   （README 里 gamma 2.2 输入变换仍在 Upcoming 未实现）
5. 不要用 Lightroom/ACR 导出的 TIFF——即使标 HDR 也不是 scene linear

【注意事项】
- 线性 EXR 无色彩标签，看图软件/达芬奇按 sRGB 直接显示会很暗，这是正常的；
  要看正常亮度请用 --preview 生成的 PNG，或在 CST 里设 输入 Linear→输出 sRGB。
- 逆 gamma 只保精度不补信息：扫描时 clipping 掉的高光救不回来（脚本会 warning）。
- JPG 不适用本流程（8bit 量化 + DCT 有损 + 4:2:0 色度抽样，暗部精度已被毁）。
- 通道铁律：np.fromfile 读出是 RGB 顺序；用 cv2.imwrite 写文件必须
  先 arr[...,[2,1,0]] 转 BGR；写 EXR 用通道名 R/G/B 直接给 RGB 语义，不要交换。

用法:
  python epson2linear_exr.py IMG.TIF [IMG2.TIF ...] [-g 1.8] [-o OUTDIR]
                             [--preview] [--float-tiff] [--no-verify]
例:
  python epson2linear_exr.py "扫描件.tif" -o "输出目录" --preview
"""

import argparse
import os
import struct
import sys
import time

import numpy as np

try:
    import OpenEXR
    import Imath
except ImportError:
    sys.exit("需要 OpenEXR 库：pip install OpenEXR")


# ---------------------------------------------------------------- 路径辅助
def _ascii_path_workaround(path, writer, *args):
    """OpenEXR C++ 后端在 Windows 对中文路径不稳：先写 ASCII 临时文件再移动。"""
    try:
        path.encode("ascii")
        writer(path, *args)
    except UnicodeEncodeError:
        import shutil
        import tempfile

        tmp = os.path.join(tempfile.gettempdir(), "exr_out_%d.exr" % os.getpid())
        writer(tmp, *args)
        shutil.move(tmp, path)


def _open_exr_safe(path):
    try:
        path.encode("ascii")
        return OpenEXR.InputFile(path)
    except UnicodeEncodeError:
        import shutil
        import tempfile

        tmp = os.path.join(tempfile.gettempdir(), "exr_in_%d.exr" % os.getpid())
        shutil.copy2(path, tmp)
        return OpenEXR.InputFile(tmp)


# ---------------------------------------------------------------- TIFF 读取
_TYPESIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


def read_tiff_ifd(path):
    """解析 classic TIFF 的 IFD0，返回 {tag: (type, count, raw_bytes, byteorder)}"""
    with open(path, "rb") as f:
        hdr = f.read(8)
        if len(hdr) < 8 or hdr[:2] not in (b"II", b"MM"):
            raise ValueError("不是 TIFF 文件：%s" % path)
        bo = "<" if hdr[:2] == b"II" else ">"
        if struct.unpack(bo + "H", hdr[2:4])[0] != 42:
            raise ValueError("只支持 classic TIFF（magic=42）")
        ifd_off = struct.unpack(bo + "I", hdr[4:8])[0]
        f.seek(ifd_off)
        n = struct.unpack(bo + "H", f.read(2))[0]
        tags = {}
        for _ in range(n):
            raw = f.read(12)
            tag, ty, cnt = struct.unpack(bo + "HHI", raw[:8])
            nbytes = _TYPESIZE.get(ty, 1) * cnt
            if nbytes <= 4:
                data = raw[8 : 8 + nbytes]
            else:
                pos = f.tell()
                off = struct.unpack(bo + "I", raw[8:12])[0]
                f.seek(off)
                data = f.read(nbytes)
                f.seek(pos)
            tags[tag] = (ty, cnt, data, bo)
        return tags


def _num(entry, index=0):
    ty, cnt, data, bo = entry
    if ty == 3:
        return struct.unpack_from(bo + "H", data, index * 2)[0]
    if ty == 4:
        return struct.unpack_from(bo + "I", data, index * 4)[0]
    if ty == 1:
        return data[index]
    raise ValueError("不支持的 TIFF 标签类型 %d" % ty)


def read_epson_tiff16_rgb(path):
    """读 Epson 未压缩 48bit TIFF，返回 (H, W, uint16 RGB 数组)。"""
    tags = read_tiff_ifd(path)
    need = [256, 257, 258, 259, 262, 273, 277]
    missing = [t for t in need if t not in tags]
    if missing:
        raise ValueError("TIFF 缺少必要标签 %s：%s" % (missing, path))

    w, h = _num(tags[256]), _num(tags[257])
    bits = [_num(tags[258], i) for i in range(3)]
    comp = _num(tags[259])
    photo = _num(tags[262])
    spp = _num(tags[277])
    planar = _num(tags[284]) if 284 in tags else 1
    bo = tags[256][3]

    if bits != [16, 16, 16] or spp != 3:
        raise ValueError("需要 48bit RGB（BitsPerSample=16,16,16, SamplesPerPixel=3），实际 %s/%d" % (bits, spp))
    if comp != 1:
        raise ValueError("需要无压缩 TIFF（Compression=1），实际 %d" % comp)
    if photo != 2:
        raise ValueError("需要 Photometric=RGB（2），实际 %d" % photo)
    if planar != 1:
        raise ValueError("需要 PlanarConfig=1（chunky），实际 %d" % planar)

    off0 = _num(tags[273], 0)
    npx = w * h * 3
    fsize = os.path.getsize(path)
    if fsize < off0 + npx * 2:
        raise ValueError("文件长度不足：需要 %d bytes，实际 %d" % (off0 + npx * 2, fsize))

    a = np.fromfile(path, dtype=bo + "u2", offset=off0, count=npx).reshape(h, w, 3)
    return a


# ---------------------------------------------------------------- 输出函数
def _write_exr_impl(path, rgb_lin):
    h, w = rgb_lin.shape[:2]
    header = OpenEXR.Header(w, h)
    header["compression"] = Imath.Compression(Imath.Compression.ZIP_COMPRESSION)
    channels = {
        c: np.ascontiguousarray(rgb_lin[..., i], dtype=np.float32).tobytes()
        for i, c in enumerate("RGB")
    }
    exr = OpenEXR.OutputFile(path, header)
    exr.writePixels(channels)
    exr.close()


def write_exr(rgb_lin, path):
    _ascii_path_workaround(path, _write_exr_impl, rgb_lin)


def srgb_encode(x):
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)


def write_preview_png(rgb_lin, path):
    """线性值 → sRGB 回编码 → 8bit RGB PNG（PIL 写，避免 cv2 的 BGR 陷阱）。"""
    from PIL import Image

    prev = (srgb_encode(rgb_lin) * 255.0).clip(0, 255).astype(np.uint8)
    Image.fromarray(prev, mode="RGB").save(path)


def write_float_tiff(rgb_lin, path):
    """32bit float TIFF（本地计算备份；达芬奇兼容性不保证，主交付用 EXR）。"""
    import cv2

    # cv2.imwrite 把数组第 0 通道当 B 写 → 先 RGB 转 BGR
    ok = cv2.imwrite(path, rgb_lin[..., [2, 1, 0]].astype(np.float32))
    if not ok:
        raise IOError("写 float TIFF 失败：%s" % path)


# ---------------------------------------------------------------- 验证
def verify_exr(exr_path, rgb16, gamma, max_median=0.95):
    """读回 EXR，与原图^gamma 抽样比对；返回报告字符串列表。"""
    f = _open_exr_safe(exr_path)
    dw = f.header()["dataWindow"]
    w = dw.max.x - dw.min.x + 1
    h = dw.max.y - dw.min.y + 1
    if (h, w) != rgb16.shape[:2]:
        raise ValueError("EXR 尺寸 %dx%d 与原图 %dx%d 不符" % (w, h, rgb16.shape[1], rgb16.shape[0]))

    ch = {
        c: np.frombuffer(f.channel(c), dtype=np.float32).reshape(h, w)
        for c in "RGB"
    }
    f.close()

    step = max(1, min(h, w) // 500)
    rep = []
    for i, c in enumerate("RGB"):
        e = ch[c][::step, ::step].astype(np.float64)
        ref = np.power(rgb16[::step, ::step, i].astype(np.float64) / 65535.0, gamma)
        corr = float(np.corrcoef(e.ravel(), ref.ravel())[0, 1])
        mae = float(np.mean(np.abs(e - ref)))
        rep.append("%s: 与原图^%.2f 相关=%.6f 平均绝对误差=%.6f" % (c, gamma, corr, mae))
        if corr < 0.999:
            rep.append("  ⚠ %s 相关偏低，检查通道顺序/写出路径" % c)

    med = float(np.median(rgb16.astype(np.float32) / 65535.0))
    if med > max_median:
        rep.append("⚠ 原图中位电平 %.3f 偏高，扫描可能曝光过度" % med)
    sat = float((rgb16 >= 65535).mean())
    if sat > 0.0001:
        rep.append("⚠ 原图有 %.4f%% 像素饱和（65535），clipping 无法靠线性化恢复" % (sat * 100))
    return rep


# ---------------------------------------------------------------- 单张处理
def process_one(src, outdir, gamma, do_preview, do_float_tiff, do_verify):
    t0 = time.time()
    base = os.path.splitext(os.path.basename(src))[0]
    tag = ("g%d" % round(gamma * 10)) if abs(gamma - round(gamma)) > 1e-9 else ("g%d" % gamma)
    stem = "%s_linear_%s" % (base, tag)
    os.makedirs(outdir, exist_ok=True)

    print("\n=== %s ===" % os.path.basename(src))
    rgb16 = read_epson_tiff16_rgb(src)
    print("读入 %dx%d uint16 RGB (%.1f MB) 用时 %.1fs" %
          (rgb16.shape[1], rgb16.shape[0], rgb16.nbytes / 1e6, time.time() - t0))

    med = np.median(rgb16.astype(np.float32) / 65535.0, axis=(0, 1))
    print("原图中位电平 R/G/B = %s（gamma 编码域）" % np.round(med, 4).tolist())
    if np.max(med) > 0.95:
        print("  ⚠ 电平偏高，注意 clipping")

    lin = np.power(rgb16.astype(np.float32) / 65535.0, gamma)
    print("线性化 gamma=%.2f → 中位 %s" % (gamma, np.round(np.median(lin, axis=(0, 1)), 4).tolist()))

    exr_path = os.path.join(outdir, stem + ".exr")
    write_exr(lin, exr_path)
    print("写出 %s (%.1f MB, %.1fs)" %
          (os.path.basename(exr_path), os.path.getsize(exr_path) / 1e6, time.time() - t0))

    if do_verify:
        for line in verify_exr(exr_path, rgb16, gamma):
            print("  " + line)

    if do_preview:
        p = os.path.join(outdir, stem + "_preview.png")
        write_preview_png(lin, p)
        print("写出 %s (%.1f MB, 仅预览)" % (os.path.basename(p), os.path.getsize(p) / 1e6))

    if do_float_tiff:
        p = os.path.join(outdir, stem + "_float32.tif")
        write_float_tiff(lin, p)
        print("写出 %s (%.1f MB, 本地备份)" % (os.path.basename(p), os.path.getsize(p) / 1e6))

    print("完成，总用时 %.1fs" % (time.time() - t0))
    return exr_path


def main():
    ap = argparse.ArgumentParser(description="Epson 48bit TIFF → scene-linear EXR (gamma=1.8)")
    ap.add_argument("inputs", nargs="+", help="输入 TIFF（可多个）")
    ap.add_argument("-g", "--gamma", type=float, default=1.8,
                    help="逆 gamma 指数，默认 1.8（本机 V700 实测）")
    ap.add_argument("-o", "--outdir", default=None,
                    help="输出目录，默认与输入文件同目录")
    ap.add_argument("--preview", action="store_true", help="额外生成 sRGB 预览 PNG")
    ap.add_argument("--float-tiff", action="store_true", help="额外生成 32bit float TIFF 备份")
    ap.add_argument("--no-verify", action="store_true", help="跳过 EXR 读回验证")
    args = ap.parse_args()

    if args.gamma <= 0 or args.gamma > 3:
        sys.exit("gamma 应在 (0, 3] 范围，实际 %s" % args.gamma)

    for src in args.inputs:
        if not os.path.isfile(src):
            sys.exit("输入文件不存在：%s" % src)
        outdir = args.outdir or os.path.dirname(os.path.abspath(src))
        process_one(src, outdir, args.gamma, args.preview, args.float_tiff, not args.no_verify)


if __name__ == "__main__":
    main()
