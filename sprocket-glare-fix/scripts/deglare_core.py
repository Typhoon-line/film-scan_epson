# -*- coding: utf-8 -*-
"""
齿孔光晕修复 · 共用图像函数 (deglare_auto / deglare_learn 共用)
===============================================================
口径固定在此处, 两个脚本不得各自复制实现:
  - 边界: 亮度谷底法 (不依赖固定 y)
  - 孔位: 白孔行 + 连通段
  - 纵深形状: 0-30px 平台, 30-190px 线性归零, 底边 0.6 倍
  - 带内抬升: 孔下基带 - 邻孔间隙基带 (信号比屏幕区大 ~40 倍, 用于逐孔相对强度)
"""
import os, cv2, numpy as np

PLAT, REACH, HALF, BOT_FACTOR = 30.0, 190.0, 170, 0.6
WHITE_LEVEL = 0.96   # 白孔亮度: 用户 2026-11-25 对比 100/95/90 后定 96 (略柔和)
MEASURE = (20, 190)
SHAPE_MEAN = ((PLAT-MEASURE[0])*1.0 + (REACH-PLAT)*0.5)/(REACH-MEASURE[0])  # 0.5294

def load(p):
    data = np.fromfile(p, dtype=np.uint8)
    im = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if im is None:
        raise ValueError(f'无法解码: {p}')
    return im

def to_unchanged_scale(im):
    if im.dtype == np.uint16:
        return im.astype(np.float32)/65535.0, 65535.0, np.uint16
    return im.astype(np.float32)/255.0, 255.0, np.uint8

def imwrite(p, out):
    """写图到任意路径 (含中文)。cv2.imwrite 在 Windows 对中文路径静默失败,
    故非 ASCII 路径改用 imencode + ndarray.tofile。"""
    try:
        p.encode('ascii')
        ok = cv2.imwrite(p, out)
        if not ok: raise RuntimeError(f'imwrite 失败: {p}')
    except UnicodeEncodeError:
        ext = os.path.splitext(p)[1] or '.png'
        ok, buf = cv2.imencode(ext, out)
        if not ok: raise RuntimeError(f'imencode 失败: {p}')
        buf.tofile(p)

def save(p, im01, dtype):
    mx = 65535.0 if dtype == np.uint16 else 255.0
    out = np.clip(im01*mx, 0, mx).astype(dtype)
    imwrite(p, out)
    return out

def lum(im01):
    b,g,r = im01[:,:,0], im01[:,:,1], im01[:,:,2]
    return 0.299*r + 0.587*g + 0.114*b

def get_white(im01):
    b,g,r = im01[:,:,0], im01[:,:,1], im01[:,:,2]
    mx = np.maximum(np.maximum(b,g), r); mn = np.minimum(np.minimum(b,g), r)
    return (mn > 0.90) & ((mx-mn) < 0.08)

def detect_bands(im01, verbose=False):
    """白孔外侧 + 亮度谷底内侧; 不硬编码 y"""
    H = im01.shape[0]
    white = get_white(im01)
    wrow = white.mean(1); Lrow = np.median(lum(im01), axis=1)
    top = bot = None
    zt = np.where(wrow[:int(H*0.3)] > 0.03)[0]
    if len(zt):
        anchor = int(zt.max()); lo, hi = anchor+2, min(H, anchor+200)
        seg = Lrow[lo:hi]
        if len(seg): top = int(lo + int(np.argmin(seg)) + 3)
    zb = np.where(wrow[int(H*0.6):] > 0.03)[0] + int(H*0.6)
    if len(zb):
        anchor = int(zb.min()); lo, hi = max(0, anchor-200), anchor-2
        seg = Lrow[lo:hi]
        if len(seg): bot = int(lo + int(np.argmin(seg)) - 2)
    if verbose: print(f'  边界探测: top={top} bot={bot} (H={H})')
    return top, bot

def hole_geom(im01):
    """返回 (hy, ye, holes[(x0,x1)], centers, gaps)"""
    white = get_white(im01)
    wrow = white.sum(1)
    hy = int(np.argmax(wrow[:1300]))
    ye = hy
    for y in range(hy, min(1300, im01.shape[0])):
        if wrow[y] < im01.shape[1]*0.02: ye = y; break
    row = white[hy].astype(np.int8)
    d = np.diff(np.pad(row, (1,1)))
    st = np.where(d==1)[0]; en = np.where(d==-1)[0]
    holes = [(int(s),int(e)) for s,e in zip(st,en) if e-s > im01.shape[1]*0.005]
    centers = [(a+b)//2 for a,b in holes]
    gaps = [(holes[i][1]+holes[i+1][0])//2 for i in range(len(holes)-1)]
    return hy, ye, holes, centers, gaps

def hole_centers(im01):
    hy, ye, holes, centers, gaps = hole_geom(im01)
    return centers, hy, holes

def warm_mask(im01):
    b,g,r = im01[:,:,0], im01[:,:,1], im01[:,:,2]
    return (r>g+0.04) & (g>b+0.02) & (r>0.45)

def band_lifts(im01, ye, top, holes, centers, gaps, depth=40):
    """逐孔带内抬升: 孔下基带 - 相邻间隙基带; 正=孔下更亮(晕)"""
    L = lum(im01); wm = warm_mask(im01)
    y0, y1 = ye+3, min(ye+3+depth, top-5)
    if y1 <= y0: return np.zeros(len(centers), np.float32)
    sub = L[y0:y1]; m = ~wm[y0:y1]
    def colmed(xc, half=140):
        lo, hi = max(0,xc-half), min(sub.shape[1],xc+half+1)
        v = sub[:,lo:hi][m[:,lo:hi]]
        return float(np.median(v)) if v.size > 50 else np.nan
    hv = np.array([colmed(c) for c in centers], np.float64)
    gv = np.array([colmed(g) for g in gaps], np.float64)
    lifts = np.zeros(len(centers), np.float64)
    for i in range(len(centers)):
        gs = []
        if i > 0: gs.append(gv[i-1])
        if i < len(gv): gs.append(gv[i])
        lifts[i] = np.nanmean(gs) - hv[i] if gs else 0.0
    return lifts

def hole_mask_1d(W, centers, half=HALF):
    m = np.zeros(W, np.float32)
    for xc in centers:
        if 0 <= xc < W:
            m[max(0,xc-half):min(W,xc+half+1)] = 1.0
    return m

def hex2bgr01(s):
    s = s.lstrip('#')
    r,g,b = int(s[0:2],16), int(s[2:4],16), int(s[4:6],16)
    return np.array([b,g,r], np.float32)/255.0

def recolor_band(im01, dark_bgr, top, bot):
    """V4: 齿孔带重构, 画面区保持原样"""
    out = im01.copy()
    b,g,r = im01[:,:,0], im01[:,:,1], im01[:,:,2]
    mx = np.maximum(np.maximum(b,g),r); mn = np.minimum(np.minimum(b,g),r)
    white_bgr = np.ones(3, np.float32) * WHITE_LEVEL
    def ss(x): x=np.clip(x,0,1); return x*x*(3-2*x)
    for y0,y1 in [(0,top),(bot,im01.shape[0])]:
        if y1<=y0: continue
        B,G,R = b[y0:y1],g[y0:y1],r[y0:y1]
        MN = mn[y0:y1]
        w = ss((MN-45/255.0)/((250-45)/255.0))
        base = dark_bgr[None,None,:]*(1-w[...,None]) + white_bgr[None,None,:]*w[...,None]
        warm_raw = ((R-G)+(G-B)-12/255.0)/(55/255.0)
        warm_str = ss(np.clip(R-90/255.0,0,60/255.0)/(60/255.0))
        o = ss(warm_raw)*warm_str
        out[y0:y1] = np.clip(base*(1-o[...,None]) + im01[y0:y1]*o[...,None], 0, 1)
    return out

def depth_shape(y, plat=PLAT, reach=REACH):
    y = np.asarray(y, np.float32)
    return np.where(y<=plat, 1.0, np.where(y<reach, (reach-y)/(reach-plat), 0.0)).astype(np.float32)

def apply_halo_field(out01, top, bot, centers, amps, half=HALF, plat=PLAT, reach=REACH, bot_factor=BOT_FACTOR):
    """逐孔幅值补偿: field(y,x)=Σ_i amp_i * depth(y) ; 直接减, 不 warp"""
    H,W = out01.shape[:2]
    ys = np.arange(H, dtype=np.float32)
    sh = np.maximum(depth_shape(ys-top,plat,reach), bot_factor*depth_shape(bot-ys,plat,reach))
    for xc, a in zip(centers, amps):
        if a == 0: continue
        x0, x1 = max(0,int(xc)-half), min(W,int(xc)+half+1)
        if x1<=x0: continue
        sub = a*sh[top:bot,None]
        if out01.ndim == 3:
            sub = sub[...,None]
        out01[top:bot, x0:x1] -= sub
    np.clip(out01, 0, 1, out=out01)

def sift_shift(a, b, scale=4):
    """对照像素(u,v)->带孔图(u+dx,v+dy); 降采样仅用于求 H, 不用于指标"""
    sa = cv2.resize((a>>8).astype(np.uint8), (a.shape[1]//scale,a.shape[0]//scale), interpolation=cv2.INTER_AREA)
    sb = cv2.resize((b>>8).astype(np.uint8), (b.shape[1]//scale,b.shape[0]//scale), interpolation=cv2.INTER_AREA)
    sift = cv2.SIFT_create(nfeatures=4000)
    ka,da = sift.detectAndCompute(cv2.cvtColor(sa,cv2.COLOR_BGR2GRAY),None)
    kb,db = sift.detectAndCompute(cv2.cvtColor(sb,cv2.COLOR_BGR2GRAY),None)
    m = cv2.BFMatcher().knnMatch(da,db,k=2)
    good = [x for x,y in m if x.distance < 0.7*y.distance]
    src = np.float32([ka[g.queryIdx].pt for g in good])
    dst = np.float32([kb[g.trainIdx].pt for g in good])
    Hm, mask = cv2.findHomography(src,dst,cv2.RANSAC,3.0)
    dx = -round(float(Hm[0,2])*scale); dy = -round(float(Hm[1,2])*scale)
    return dx, dy, int(mask.sum()), len(good)

def pair_delta(with_im, ctl_im, dx, dy, centers, measure=MEASURE):
    """对照差分: 返回 (总delta, 逐孔delta, SNR, 裁窗信息)
    D = L(对照) - L(带孔); 孔下比孔间更亮 => delta>0 => 光晕"""
    Hh,Ww = ctl_im.shape[:2]
    u0,u1 = max(0,-dx), min(Ww, with_im.shape[1]-dx)
    v0,v1 = max(0,-dy), min(Hh, with_im.shape[0]-dy)
    D = lum(ctl_im[v0:v1,u0:u1]) - lum(with_im[v0+dy:v1+dy,u0+dx:u1+dx])
    hx = hole_mask_1d(Wu:=u1-u0, [int(c)-dx-u0 for c in centers])
    gx = ~hx.astype(bool)
    seg = D[measure[0]:measure[1]]
    dh = float(np.median(seg[:,hx.astype(bool)]))
    dg = float(np.median(seg[:,gx]))
    delta = dg - dh
    # 逐孔
    pitch = int(np.median(np.diff(centers))) if len(centers)>1 else 900
    per = []
    for c in centers:
        xc = int(c)-dx-u0
        lo,hi = max(0,xc-HALF), min(Wu,xc+HALF+1)
        if hi<=lo: per.append(np.nan); continue
        vh = np.median(seg[:,lo:hi])
        gs=[]
        for gx0 in (xc-pitch//2, xc+pitch//2):
            a,b = max(0,gx0-60), min(Wu,gx0+61)
            if b>a: gs.append(np.median(seg[:,a:b]))
        per.append(float(np.mean(gs)-vh) if gs else np.nan)
    per = np.array(per, np.float64)
    snr = abs(delta)/(np.nanstd(per)/max(1,np.sqrt(np.isfinite(per).sum()))) if np.isfinite(per).any() else 0.0
    return dict(delta=float(delta), per=per, snr=float(snr), crop=(v0,v1,u0,u1))

# ---------- 批次识别 ----------
# 目标: 批内用同一套参数(表现稳定), 批间允许波动(不收敛成全局固定值)。
# 判据(用户定): 连续编号 + 相邻文件生成时间约 5min 内 => 同一批; 断档/超时 => 新批。
import re as _re, datetime as _dt

def file_index(path):
    """从文件名末尾提取序号 img001.tif -> 1; 无序号返回 None"""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _re.search(r'(\d+)$', stem)
    return int(m.group(1)) if m else None

def file_mtime(path):
    return os.path.getmtime(path)

def group_batches(files, gap_min=5.0, gap_index=1):
    """把文件列表分成扫描批次。
    规则: 按文件名序号排序后, 相邻两张满足 (序号差<=gap_index) 且
    (mtime 间隔<=gap_min 分钟) 则同批; 否则切新批。无序号的文件各自成批。
    返回 [(key, [file,...]), ...], key 用批内首张 'img001_20261125' 形式。"""
    items = []
    for f in files:
        idx = file_index(f)
        items.append(dict(path=f, idx=idx, mt=file_mtime(f)))
    items.sort(key=lambda d: (d['idx'] if d['idx'] is not None else 10**9, d['mt']))
    batches, cur = [], []
    for it in items:
        if not cur:
            cur = [it]; continue
        p = cur[-1]
        contiguous = (it['idx'] is not None and p['idx'] is not None
                      and 0 < it['idx'] - p['idx'] <= gap_index)
        in_time = abs(it['mt'] - p['mt']) <= gap_min * 60.0
        if contiguous and in_time:
            cur.append(it)
        else:
            batches.append(cur); cur = [it]
    if cur: batches.append(cur)
    out = []
    for b in batches:
        first = b[0]
        day = _dt.datetime.fromtimestamp(first['mt']).strftime('%Y%m%d')
        base = os.path.splitext(os.path.basename(first['path']))[0]
        key = f'{base}_{day}'
        out.append((key, [x['path'] for x in b]))
    return out
