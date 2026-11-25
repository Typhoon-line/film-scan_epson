# -*- coding: utf-8 -*-
"""
JPG 齿孔带重着色 (8bit, 只做齿孔带, 不动成像区)
=================================================
适用判断:
  - 齿孔带重着色 = 大色块替换, 8bit 足够 -> 适用
  - 成像区光晕补偿 = 亚 DN 级弱信号(8bit 仅 0.28DN < 噪声 0.9-1.2DN) -> 不适用, 跳过
口径直接复用 deglare_core (白孔 96% 已固化), 与 TIFF 流程完全一致。
输出无损 PNG, 避免 JPG 二次压缩在齿孔边缘引入伪影。

用法:
  python jpg_recolor.py --src <目录> --out <目录> img051 img055 img062
  python jpg_recolor.py --src <目录> --out <目录> --glob 'img0*.jpg'

默认 --src=. --out=<src>/after ; 给文件名(不含扩展名)或 --glob 模式。
"""
import os, sys, glob, argparse, cv2, numpy as np
import deglare_core as C

def one(src, out_dir, stem):
    p = os.path.join(src, stem if stem.lower().endswith(('.jpg','.jpeg','.png','.tif','.tiff'))
                     else stem + '.jpg')
    if not os.path.isfile(p):
        print(f'!! 不存在 {p}'); return None
    im = C.load(p)
    a01, mx, dtype = C.to_unchanged_scale(im)
    H, W = a01.shape[:2]
    top, bot = C.detect_bands(a01)
    if top is None or bot is None:
        print(f'!! {stem} 边界探测失败, 跳过'); return None
    dark = C.hex2bgr01('201B07')
    out = C.recolor_band(a01, dark, top, bot)   # 白孔 96% 在 core 里已固化

    base = os.path.splitext(os.path.basename(p))[0]
    o = os.path.join(out_dir, f'{base}_recolored.png')
    C.save(o, out, dtype)
    # 交界预览: 顶带下缘上下各取一段, 原图|修复 并排
    y0, y1 = max(0, top - 60), min(H, top + 160)
    strip = np.concatenate([a01[y0:y1, ::6], out[y0:y1, ::6]], axis=1)
    pv = os.path.join(out_dir, f'{base}_preview.png')
    C.imwrite(pv, (np.clip(strip, 0, 1) * 255).astype(np.uint8))
    print(f'{base}: top={top} bot={bot} 位深={np.dtype(dtype).name} 白孔={int(255*C.WHITE_LEVEL)} -> {o}')
    return dict(stem=base, a01=a01, out=out, top=top, bot=bot)

def main():
    ap = argparse.ArgumentParser(description='JPG 齿孔带重着色 (仅齿孔带)')
    ap.add_argument('stems', nargs='*', help='文件名(可带扩展名)列表')
    ap.add_argument('--src', default='.', help='输入目录, 默认当前目录')
    ap.add_argument('--out', default=None, help='输出目录, 默认 <src>/after')
    ap.add_argument('--glob', default=None, help='glob 模式批量, 如 "img0*.jpg"')
    args = ap.parse_args()
    out_dir = args.out or os.path.join(args.src, 'after')
    os.makedirs(out_dir, exist_ok=True)

    stems = list(args.stems)
    if args.glob:
        stems += [os.path.basename(x) for x in sorted(glob.glob(os.path.join(args.src, args.glob)))]
    if not stems:
        ap.error('给文件名, 或用 --glob 指定批量模式')
    items = [r for s in stems if (r := one(args.src, out_dir, s))]

    if items:
        rows = []
        for it in items:
            t, o, top = it['a01'], it['out'], it['top']
            seg = np.concatenate([t[max(0,top-50):top+120:2, ::8],
                                  o[max(0,top-50):top+120:2, ::8]], axis=1)
            seg = (np.clip(seg,0,1)*255).astype(np.uint8)
            seg = cv2.copyMakeBorder(seg, 24, 0, 0, 0, cv2.BORDER_CONSTANT, value=(40,40,40))
            cv2.putText(seg, f"{it['stem']}  left=原图  right=修复(白96%)", (6,16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255,255,255), 1)
            rows.append(seg)
        w = max(x.shape[1] for x in rows)
        rows = [cv2.copyMakeBorder(x,0,0,0,w-x.shape[1],cv2.BORDER_CONSTANT,value=(40,40,40)) for x in rows]
        ov = os.path.join(out_dir, 'batch_overview.png')
        C.imwrite(ov, np.vstack(rows))
        print('总览 ->', ov)

if __name__ == '__main__':
    main()
