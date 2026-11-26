# -*- coding: utf-8 -*-
"""
白孔亮度档位对比/重生成 (可选工具)
====================================
从原图按 V2 完全相同逻辑重生成, 唯一差异: 白孔亮度 = MAXV × level。
齿孔带: 底色201B07 / 白孔=MAXV*level / 橘字软保留
成像区: 逐图对照实测 amp (heal_log.json), 与 V2 完全一致
边界 BANDS / 孔 mask 全部沿用固定标定值。

白度档位已定 96% (deglare_core.WHITE_LEVEL); 本工具用于对比其它档位。
输出: <out>/<tag>/imgNNN_healed.tif

用法:
  python white_variant.py --src <原图目录> --out <输出目录> --heal <heal_log.json> \
      --levels w100=1.00,w96=0.96 --bands '1:978,5632;2:954,5610;...'

BANDS 是每幅图的固定边界(top,bot), 必须随数据提供; 可对原图跑
deglare_core.detect_bands 探测后写入, 或对已知底片沿用标定值。
"""
import os, json, argparse, cv2, numpy as np
MAXV = 65535.0

def imwrite_any(path, img, params=None):
    """中/英文路径与文件名通用：imencode+tofile 避开 cv2.imwrite 在 Windows 中文路径静默失败。"""
    import os as _os, cv2 as _cv2
    ext = _os.path.splitext(path)[1] or '.png'
    ok, buf = _cv2.imencode(ext, img) if params is None else _cv2.imencode(ext, img, params)
    if not ok:
        raise RuntimeError('图像编码失败: %s' % path)
    buf.tofile(path)
    return True


def load(p): return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
def hex2bgr16(s):
    s = s.lstrip('#'); r,g,b = int(s[0:2],16),int(s[2:4],16),int(s[4:6],16)
    return np.array([b,g,r], np.float32)/255.0*MAXV

def hole_centers(im):
    b,g,r = [im[:,:,i].astype(np.float32)/MAXV for i in range(3)]
    mx = np.maximum(np.maximum(b,g),r); mn = np.minimum(np.minimum(b,g),r)
    white = (mn>0.9) & ((mx-mn)<0.08)
    hy = int(np.argmax(white[:1200].sum(1)))
    row = white[hy].astype(np.int8); d = np.diff(np.pad(row,(1,1)))
    st = np.where(d==1)[0]; en = np.where(d==-1)[0]
    return [int((a+b)//2) for a,b in zip(st,en)], hy

def hole_mask_1d(W, centers, half=170):
    m = np.zeros(W, np.float32)
    for xc in centers:
        if 0 <= xc < W: m[max(0,xc-half):min(W,xc+half+1)] = 1.0
    return m

PLAT, REACH, HALF, BOT_FACTOR = 30.0, 190.0, 170, 0.6
def shape(y):
    y = np.asarray(y, np.float32)
    return np.where(y<=PLAT,1.0,np.where(y<REACH,(REACH-y)/(REACH-PLAT),0.0)).astype(np.float32)

def recolor_band(im, dark_bgr, top, bot, level):
    orig = im.astype(np.float32); out = orig.copy()
    b,g,r = [orig[:,:,i] for i in range(3)]
    mx = np.maximum(np.maximum(b,g),r); mn = np.minimum(np.minimum(b,g),r)
    white_bgr = np.array([MAXV,MAXV,MAXV],np.float32)*level
    def ss(x): x=np.clip(x,0,1); return x*x*(3-2*x)
    for y0,y1 in [(0,top),(bot,im.shape[0])]:
        if y1<=y0: continue
        B,G,R = b[y0:y1],g[y0:y1],r[y0:y1]; MN = mn[y0:y1]
        w = ss((MN-45/255*MAXV)/((250-45)/255*MAXV))
        base = dark_bgr[None,None,:]*(1-w[...,None]) + white_bgr[None,None,:]*w[...,None]
        warm_raw = ((R-G)+(G-B)-12/255*MAXV)/(55/255*MAXV)
        warm_str = ss(np.clip(R-90/255*MAXV,0,60/255*MAXV)/(60/255*MAXV))
        o = ss(warm_raw)*warm_str
        out[y0:y1] = np.clip(base*(1-o[...,None])+orig[y0:y1]*o[...,None],0,MAXV)
    return np.clip(out,0,MAXV).astype(np.uint16)

def build(a, top, bot, centers, amp, dark, level):
    H,W = a.shape[:2]
    out = recolor_band(a,dark,top,bot,level)
    hx = hole_mask_1d(W,centers)
    ys = np.arange(H,dtype=np.float32)
    sh = np.maximum(shape(ys-top), BOT_FACTOR*shape(bot-ys))
    field = (amp*MAXV)*sh[:,None]*hx[None,:]
    out[top:bot] = np.clip(out[top:bot].astype(np.float32)-field[top:bot,:,None],0,MAXV).astype(np.uint16)
    return out

def parse_bands(s):
    """'1:978,5632;2:954,5610' -> {1:(978,5632),2:(954,5610)}"""
    d = {}
    if not s: return d
    for part in s.split(';'):
        k, vals = part.split(':'); t,b = vals.split(',')
        d[int(k)] = (int(t),int(b))
    return d

def main():
    ap = argparse.ArgumentParser(description='白孔亮度档位对比/重生成')
    ap.add_argument('--src', required=True, help='原图目录')
    ap.add_argument('--out', required=True, help='输出根目录')
    ap.add_argument('--heal', required=True, help='heal_log.json (逐图 amp)')
    ap.add_argument('--levels', default='w100=1.00,w96=0.96', help='档位=亮度, 逗号分隔')
    ap.add_argument('--bands', required=True, help='每图边界 "1:t,b;2:t,b;..."')
    ap.add_argument('--dark', default='201B07')
    args = ap.parse_args()
    levels = [(kv.split('=')[0], float(kv.split('=')[1])) for kv in args.levels.split(',')]
    BANDS = parse_bands(args.bands)
    heal = json.load(open(args.heal, encoding='utf-8'))
    dark = hex2bgr16(args.dark)
    os.makedirs(args.out, exist_ok=True)
    for tag, level in levels:
        od = os.path.join(args.out, tag); os.makedirs(od, exist_ok=True)
        print(f'\n=== {tag} level={level} -> {od} ===')
        for n,(top,bot) in sorted(BANDS.items()):
            a = load(f'{args.src}/img{n:03d}.tif')
            if a is None: print(f'  !! img{n:03d}.tif 读取失败, 跳过'); continue
            centers,hy = hole_centers(a)
            amp = float(heal[f'{n:03d}']['amp'])
            out = build(a, top, bot, centers, amp, dark, level)
            imwrite_any(os.path.join(od, f'img{n:03d}_healed.tif'), out)
            print(f'  img{n:03d}: white={int(MAXV*level)} (8bit≈{int(255*level)})')
            del a,out
    print('done')

if __name__ == '__main__': main()
