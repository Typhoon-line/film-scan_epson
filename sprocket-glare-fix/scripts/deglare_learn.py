# -*- coding: utf-8 -*-
"""
齿孔光晕自学习档案 (每扫描流程独立)
=====================================
物理前提: 光晕是扫描仪光学系统固定属性, 与底片内容无关。
因此"同一扫描流程"(如 4800dpi/16bit TIFF/无色彩校正) 下的多次扫描可互相学习。

核心思想 (不做逐图噪声过拟合):
  amp_i = amp_center × hole_factor_i
  - amp_center: 由所有对照组样本的稳健集中值学习, 向先验(一次性标定)收缩
  - hole_factor_i: 逐孔微差, 由带内 lift 的跨扫描均值学习, 强收缩(默认 0.35)且限幅 ±15%
    -> 满足用户"以固定值上下微小区间、每个齿孔不必完全一致"的要求
  - 每来一批新扫描(带对照组)就追加样本、重新拟合, 效果越来越好

数据文件: deglare_profiles.json
  profiles[profile_name] = {
    meta: {dpi,bits,mode,desc,created},
    amp_center, amp_prior, amp_prior_weight, n_amp,
    hole_factor: [f1..f8], n_hole,
    samples: [{file, delta, amp, snr, date}],
    band_obs: [{file, lifts_norm, date}],
    verify: [{file, before, after, reduction, date}]
  }

CLI:
  python deglare_learn.py init --name PROFILE --dpi 4800 --bits 16 --mode no-color
  python deglare_learn.py seed --name PROFILE --data-dir references/calibration --pairs 1,7;2,8;...
  python deglare_learn.py add  --name PROFILE --with A.tif --without B.tif
  python deglare_learn.py band --name PROFILE A.tif
  python deglare_learn.py show [--name PROFILE]
  python deglare_learn.py export --name PROFILE --out profile.json
"""
import os, sys, json, argparse, datetime, math
import numpy as np
import cv2
import deglare_core as C

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, 'deglare_profiles.json')
PRIOR_AMP = 0.0010
PRIOR_W = 3.0          # 先验等效样本数 (控制噪声下不过度学习)
HOLE_SHRINK = 0.35     # 逐孔系数收缩强度 (0=全固定, 1=完全信任观测)
HOLE_CAP = 0.15        # 逐孔系数限幅 ±15%

def _now(): return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def load_db():
    if not os.path.exists(DB_PATH): return {'version':1,'profiles':{}}
    with open(DB_PATH,encoding='utf-8') as f: return json.load(f)

def save_db(db):
    tmp = DB_PATH + '.tmp'
    with open(tmp,'w',encoding='utf-8') as f: json.dump(db,f,ensure_ascii=False,indent=2)
    os.replace(tmp,DB_PATH)

def get_profile(db, name):
    if name not in db['profiles']:
        db['profiles'][name] = dict(meta=dict(name=name, created=_now()),
            amp_center=PRIOR_AMP, amp_prior=PRIOR_AMP, amp_prior_weight=PRIOR_W,
            n_amp=0, n_hole=0, hole_factor=[1.0]*8,
            hole_shrink=HOLE_SHRINK, hole_cap=HOLE_CAP,
            samples=[], band_obs=[], verify=[], batches={})
    p = db['profiles'][name]
    p.setdefault('batches', {})   # 向后兼容旧档案
    return p

def refit_amp(p):
    """稳健集中: 对照组 amp 中位数, 与先验按等效样本数加权; 剔除 SNR 过低样本"""
    vals=[]; w=[]
    for s in p['samples']:
        a = s.get('amp')
        if a is None: continue
        # 低 SNR 样本权重降低, 但不丢弃(噪声主导, 多扫几次自然收敛)
        wi = min(1.0, max(0.15, float(s.get('snr',1.0))/1.5))
        vals.append(float(a)); w.append(wi)
    if not vals:
        p['amp_center'] = p['amp_prior']; return
    vals=np.array(vals); w=np.array(w)
    # 中位数型稳健: 用 20% 截尾均值抵抗离群, 同时保留多数量信息
    lo,hi = np.percentile(vals,[20,80])
    sel=(vals>=lo)&(vals<=hi)
    if sel.sum()==0: sel=np.ones(len(vals),bool)
    m = float(np.average(vals[sel], weights=w[sel]))
    wp = float(p['amp_prior_weight']); wn = float(w.sum())
    p['amp_center'] = float((wp*p['amp_prior'] + wn*m)/(wp+wn))
    p['n_amp'] = len(vals)

def get_batch(p, key):
    """取(或建)一个批次记录。批次内所有图共用该批 amp, 批间各自波动。"""
    b = p.setdefault('batches', {}).setdefault(key, dict(
        amp_prior=PRIOR_AMP, amp=None, n=0, samples=[], date=_now()))
    b.setdefault('samples', [])
    return b

def refit_batch(p, key):
    """用该批自己的样本稳健拟合批次 amp; 不触碰全局 amp_center。
    批内多张对照 -> 批内稳定; 新批次独立拟合 -> 批间可波动。"""
    b = get_batch(p, key)
    vals=[]; w=[]
    for s in b['samples']:
        a = s.get('amp')
        if a is None: continue
        wi = min(1.0, max(0.15, float(s.get('snr',1.0))/1.5))
        vals.append(float(a)); w.append(wi)
    if not vals:
        b['amp'] = float(b['amp_prior']); return b
    vals=np.array(vals); w=np.array(w)
    lo,hi = np.percentile(vals,[20,80])
    sel=(vals>=lo)&(vals<=hi)
    if sel.sum()==0: sel=np.ones(len(vals),bool)
    m = float(np.average(vals[sel], weights=w[sel]))
    wp = PRIOR_W; wn = float(w.sum())
    b['amp'] = float((wp*float(b['amp_prior']) + wn*m)/(wp+wn))
    b['n'] = len(vals)
    return b

def refit_hole(p):
    """逐孔系数 = 1 + shrink × (跨扫描归一化 lift 均值 - 1), 限幅"""
    obs = [o['lifts_norm'] for o in p['band_obs'] if o.get('lifts_norm')]
    if not obs: return
    A = np.array(obs, np.float64)
    n = len(obs)
    mean = np.nanmean(A, axis=0)
    shrink = float(p.get('hole_shrink',HOLE_SHRINK)) * n/(n+3.0)
    cap = float(p.get('hole_cap',HOLE_CAP))
    f = 1.0 + shrink*(mean-1.0)
    f = np.clip(f, 1.0-cap, 1.0+cap)
    p['hole_factor'] = [round(float(x),4) for x in f]
    p['n_hole'] = n

def factor_for_centers(p, centers):
    """按孔号给系数; 孔数不符时循环补齐"""
    f = p.get('hole_factor') or [1.0]*8
    if not f: f=[1.0]*8
    return np.array([f[i%len(f)] for i in range(len(centers))], np.float64)

def amp_for_centers(p, centers, strength=1.0):
    return factor_for_centers(p,centers) * float(p['amp_center']) * float(strength)

def load_pair_images(with_path, ctl_path):
    a = C.load(with_path); b = C.load(ctl_path)
    return a,b

def add_control(db, name, with_path, ctl_path, note='', batch=None):
    p = get_profile(db,name)
    a,b = load_pair_images(with_path, ctl_path)
    a01,_,_ = C.to_unchanged_scale(a); b01,_,_ = C.to_unchanged_scale(b)
    centers,hy,holes = C.hole_centers(a01)
    dx,dy,inl,ng = C.sift_shift(a,b)
    r = C.pair_delta(a01,b01,dx,dy,centers)
    amp = max(0.0, r['delta'])/C.SHAPE_MEAN
    samp = dict(file=os.path.basename(with_path), control=os.path.basename(ctl_path),
        dx=dx, dy=dy, inliers=inl, delta=round(r['delta'],6), amp=round(float(amp),6),
        snr=round(r['snr'],3), date=_now(), note=note, batch=batch)
    p['samples'].append(samp)
    # 同时记录带内逐孔 lift (归一化) 供逐孔学习
    top,bot = C.detect_bands(a01)
    if top and bot:
        hy,ye,holes,centers2,gaps = C.hole_geom(a01)
        lifts = C.band_lifts(a01,ye,top,holes,centers2,gaps)
        m = float(np.mean(lifts))
        if m>1e-6:
            p['band_obs'].append(dict(file=os.path.basename(with_path),
                lifts_norm=[round(float(x/m),4) for x in lifts], date=_now(), batch=batch))
    refit_amp(p); refit_hole(p)
    binfo = None
    if batch:
        bd = get_batch(p, batch)
        bd['samples'].append(samp)
        refit_batch(p, batch)
        binfo = dict(batch=batch, amp=bd['amp'], n=bd['n'])
    return dict(delta=r['delta'], amp=amp, snr=r['snr'], dx=dx,dy=dy,
                inliers=inl, n_holes=len(centers), amp_center=p['amp_center'],
                n_amp=p['n_amp'], hole_factor=p['hole_factor'], n_hole=p['n_hole'],
                batch=binfo)

def add_band_obs(db, name, path):
    """无对照组也记录带内逐孔 lift -> 可学习逐孔系数, 但不改变 amp_center"""
    p = get_profile(db,name)
    a = C.load(path); a01,_,_ = C.to_unchanged_scale(a)
    top,bot = C.detect_bands(a01)
    if top is None or bot is None: return dict(ok=False, reason='边界探测失败')
    hy,ye,holes,centers,gaps = C.hole_geom(a01)
    lifts = C.band_lifts(a01,ye,top,holes,centers,gaps)
    m = float(np.mean(lifts))
    if m<=1e-6: return dict(ok=False, reason='带内 lift 无效')
    p['band_obs'].append(dict(file=os.path.basename(path),
        lifts_norm=[round(float(x/m),4) for x in lifts], date=_now()))
    refit_hole(p)
    return dict(ok=True, lifts=[float(x) for x in lifts], hole_factor=p['hole_factor'], n_hole=p['n_hole'])

def seed_from_existing(db, name, data_dir, pairs, bands_path=None):
    """用若干对已有对照样本一次性建档 (避免重跑 SIFT 大图)。
    data_dir 需含 heal_log.json / per_hole_band_probe.json / halo_control_diff2.json。
    标定数据格式见 references/标定数据说明.md。
    """
    p = get_profile(db,name)
    heal = json.load(open(os.path.join(data_dir,'heal_log.json'),encoding='utf-8'))
    perhole = json.load(open(os.path.join(data_dir,'per_hole_band_probe.json'),encoding='utf-8'))
    align = json.load(open(os.path.join(data_dir,'halo_control_diff2.json'),encoding='utf-8'))['stats']
    BANDS = None
    if bands_path and os.path.exists(str(bands_path)):
        try: BANDS = json.load(open(bands_path,encoding='utf-8'))
        except Exception: BANDS = None
    for pair in pairs:
        on,cn = [int(x) for x in pair.split(',')]
        key=f'{on:03d}_{cn:03d}'; s=align[key]; h=heal[f'{on:03d}']
        amp=float(h['amp']); delta=float(h['delta'])
        # SNR 保守估计: 现有逐孔 std / sqrt(n)
        ph = next((r for r in perhole if r['n']==on),None)
        snr=1.0
        if ph:
            sd=float(np.nanstd(np.array(ph['screen'],float)))
            snr=abs(delta)/(sd/max(1,math.sqrt(len(ph['screen'])))) if sd>0 else 1.0
        p['samples'].append(dict(file=f'img{on:03d}.tif', control=f'img{cn:03d}.tif',
            dx=s['dx'],dy=s['dy'],inliers=s['inliers'],delta=round(delta,6),
            amp=round(amp,6),snr=round(float(snr),3),date=_now(),note='seed'))
        if ph and ph.get('lifts'):
            v=np.array(ph['lifts'],float); m=float(np.mean(v))
            p['band_obs'].append(dict(file=f'img{on:03d}.tif',
                lifts_norm=[round(float(x/m),4) for x in v],date=_now()))
        _=BANDS  # 边界已由探针给出, 此处仅为接口一致
    refit_amp(p); refit_hole(p)
    return p

def show(db, name=None):
    for nm,p in db['profiles'].items():
        if name and nm!=name: continue
        print(f'\n=== {nm} ===')
        print(f'  meta: {p["meta"]}')
        print(f'  amp_center={p["amp_center"]:.6f} (先验 {p["amp_prior"]}, n={p["n_amp"]})')
        print(f'  hole_factor={p["hole_factor"]} (n_hole={p["n_hole"]}, shrink={p["hole_shrink"]}, cap=±{p["hole_cap"]})')
        for s in p['samples'][-10:]:
            print(f'    sample {s["file"]}: delta={s["delta"]:+.5f} amp={s["amp"]:.5f} snr={s["snr"]:.2f} '
                  f'd=({s["dx"]:+d},{s["dy"]:+d}) inl={s.get("inliers","-")}')
        print(f'  band_obs n={len(p["band_obs"])}, verify n={len(p.get("verify",[]))}')
        batches=p.get('batches',{})
        if batches:
            print(f'  batches ({len(batches)} 批):')
            for k,bd in batches.items():
                print(f'    {k}: amp={bd.get("amp"):.6f} n={bd.get("n",0)} (批内共用此值)')

def main():
    ap=argparse.ArgumentParser(description='齿孔光晕自学习档案')
    sub=ap.add_subparsers(dest='cmd')
    q=sub.add_parser('init'); q.add_argument('--name',required=True); q.add_argument('--dpi',type=int)
    q.add_argument('--bits',type=int); q.add_argument('--mode',default='no-color'); q.add_argument('--desc',default='')
    q=sub.add_parser('seed'); q.add_argument('--name',required=True)
    q.add_argument('--data-dir',default=os.path.join(os.path.dirname(HERE),'references','calibration'),
                   help='标定数据目录, 含 heal_log.json / per_hole_band_probe.json / halo_control_diff2.json')
    q.add_argument('--pairs',default='1,7;2,8;3,9;4,10;5,11;6,12')
    q.add_argument('--bands',default=None)
    q=sub.add_parser('add'); q.add_argument('--name',required=True); q.add_argument('--with',dest='with_path',required=True)
    q.add_argument('--without',dest='ctl_path',required=True); q.add_argument('--note',default='')
    q.add_argument('--batch',default=None,help='归入批次key(批内共用amp,建议用 batch 子命令自动分组得到的key)')
    q=sub.add_parser('batch'); q.add_argument('paths',nargs='+',help='一组图,自动按 连续编号+5min间隔 分批次')
    q.add_argument('--gap',type=float,default=5.0,help='相邻生成时间最大间隔(分钟),默认5')
    q=sub.add_parser('band'); q.add_argument('--name',required=True); q.add_argument('path')
    q=sub.add_parser('show'); q.add_argument('--name')
    q=sub.add_parser('export'); q.add_argument('--name',required=True); q.add_argument('--out')
    args=ap.parse_args()
    db=load_db()
    if args.cmd=='init':
        p=get_profile(db,args.name)
        p['meta'].update(dpi=args.dpi,bits=args.bits,mode=args.mode,desc=args.desc)
        save_db(db); print('init',args.name)
    elif args.cmd=='seed':
        p=seed_from_existing(db,args.name,args.data_dir,args.pairs.split(';'),args.bands)
        save_db(db); print('seed',args.name,'samples=',len(p['samples']),'amp_center=',round(p['amp_center'],6))
    elif args.cmd=='add':
        r=add_control(db,args.name,args.with_path,args.ctl_path,args.note,args.batch)
        save_db(db); print(json.dumps(r,ensure_ascii=False,indent=1))
    elif args.cmd=='batch':
        for key,files in C.group_batches(args.paths, args.gap):
            print(f'{key}: {len(files)} 张  {[os.path.basename(f) for f in files]}')
    elif args.cmd=='band':
        r=add_band_obs(db,args.name,args.path)
        save_db(db); print(json.dumps(r,ensure_ascii=False,indent=1))
    elif args.cmd=='show':
        show(db,args.name)
    elif args.cmd=='export':
        p=get_profile(db,args.name)
        obj={k:p[k] for k in ('meta','amp_center','amp_prior','amp_prior_weight','n_amp',
             'hole_factor','n_hole','hole_shrink','hole_cap')}
        out=args.out or os.path.join(HERE,f'{args.name}_profile.json')
        json.dump(obj,open(out,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
        print('exported',out)
    else: ap.print_help()

if __name__=='__main__': main()
