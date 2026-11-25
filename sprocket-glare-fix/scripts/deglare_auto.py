# -*- coding: utf-8 -*-
"""
齿孔光晕自动修复 · 无对照组通用流程 + 自学习档案
====================================================
1. 自动探测齿孔带边界 top/bot (亮度谷底法, 不依赖固定 y)
2. 自动探测白孔 x 位置
3. 齿孔带 V4 重构 (底色/白孔纯白/橘字软保留)
4. 成像区光晕补偿 = amp_center × hole_factor_i × 纵深衰减
   - 默认读 deglare_profiles.json 的自学习档案 (--profile)
   - 不指定档案则用一次性标定常数 AMP_CAL=0.001 (旧行为)
   - --learn-band 可把本图带内逐孔 lift 记录下来, 无对照组也能学逐孔系数

为何不再逐图定 amp:
  对照组逐图 amp 波动 5 倍但信噪比全 <1.5 = 噪声主导; 带内 lift 与屏幕区 amp
  相关仅 ρ=0.26。故 amp_center 走稳健集中+先验收缩, 逐孔差异走带内稳定梯度
  (跨扫描 ICC=0.69)并强收缩限幅, 避免拿噪声过拟合。

用法:
  python deglare_auto.py <图或目录>                        # 固定标定
  python deglare_auto.py <图或目录> --profile 4800dpi_tiff16_nocc
  python deglare_auto.py <图或目录> --profile ... --learn-band  # 处理+学习逐孔
  python deglare_auto.py <图> --probe                       # 只看探测结果
  python deglare_auto.py <图> --strength 1.5                # 临时整体倍率
  python deglare_auto.py <图> --no-screen / --top 978 / --bot 5632 / --dark 201B07
"""
import os, sys, glob, cv2, numpy as np, argparse, json
import deglare_core as C

AMP_CAL = 0.0010
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, 'deglare_profiles.json')

def load_profile(name):
    if not name or not os.path.exists(DB_PATH): return None
    db = json.load(open(DB_PATH,encoding='utf-8'))
    return db['profiles'].get(name)

def append_band_obs(name, path, top, bot, a01):
    """无对照组也能学习逐孔系数: 记录带内 lift 归一化形态"""
    if not name or not os.path.exists(DB_PATH): return None
    hy,ye,holes,centers,gaps = C.hole_geom(a01)
    lifts = C.band_lifts(a01,ye,top,holes,centers,gaps)
    m = float(np.mean(lifts))
    if m<=1e-6: return None
    db = json.load(open(DB_PATH,encoding='utf-8'))
    if name not in db['profiles']: return None
    p = db['profiles'][name]
    p.setdefault('band_obs',[]).append(dict(file=os.path.basename(path),
        lifts_norm=[round(float(x/m),4) for x in lifts], date='auto'))
    import deglare_learn as L
    L.refit_hole(p)
    tmp=DB_PATH+'.tmp'; json.dump(db,open(tmp,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
    os.replace(tmp,DB_PATH)
    return p['hole_factor']

def process(path, args, dark_bgr, profile):
    im = C.load(path)
    a01, _, dtype = C.to_unchanged_scale(im)
    H,W = a01.shape[:2]
    name = os.path.splitext(os.path.basename(path))[0]

    top, bot = C.detect_bands(a01, verbose=True)
    if args.top is not None: top = args.top
    if args.bot is not None: bot = args.bot
    centers, hy, holes = C.hole_centers(a01)
    print(f'  孔位: 孔行y={hy} 孔数={len(centers)} 中心x={centers[:4]}{"..." if len(centers)>4 else ""}')

    if args.probe:
        print(f'  [probe] top={top} bot={bot} 孔数={len(centers)}')
        return

    if top is None or bot is None:
        print(f'  !! {name} 边界探测失败, 请用 --top/--bot 手动指定, 跳过'); return

    out = C.recolor_band(a01, dark_bgr, top, bot)

    amp_desc = f'{AMP_CAL*args.strength:.4f} (固定标定)'
    if profile and not args.no_screen:
        amps = np.array([float(profile['amp_center'])*args.strength
                         for _ in centers], np.float64)
        f = profile.get('hole_factor') or [1.0]*8
        amps = amps * np.array([f[i%len(f)] for i in range(len(centers))])
        amp_desc = (f'档案 center={profile["amp_center"]:.5f} × '
                    f'逐孔{np.round(amps/profile["amp_center"]/args.strength,3).tolist()}')
    elif args.amp is not None:
        amps = np.array([args.amp*args.strength]*len(centers), np.float64)
    else:
        amps = np.array([AMP_CAL*args.strength]*len(centers), np.float64)

    if not args.no_screen:
        C.apply_halo_field(out, top, bot, centers, amps)
        if args.learn_band:
            hf = append_band_obs(args.profile, path, top, bot, a01)
            if hf: print(f'  [学习] 带内逐孔系数已更新: {hf}')

    o = os.path.join(args.out, f'{name}_deglared.tif')
    C.save(o, out, dtype)
    print(f'  -> {o}  (成像区 amp: {amp_desc}, {"含补偿" if not args.no_screen else "仅齿孔带"})')

    if args.preview:
        strip = np.concatenate([a01[top-40:top+240:2, ::5], out[top-40:top+240:2, ::5]], axis=1)
        C.imwrite(os.path.join(args.out, f'{name}_preview.png'), (strip*255).astype(np.uint8))

def main():
    ap = argparse.ArgumentParser(description='齿孔光晕自动修复(无对照组/自学习)')
    ap.add_argument('input', help='输入 TIFF/PNG/JPG 文件或目录')
    ap.add_argument('--out', help='输出目录 (默认 <源目录>/after)')
    ap.add_argument('--dark', default='201B07')
    ap.add_argument('--strength', type=float, default=1.0, help='整体补偿倍率 (默认1.0)')
    ap.add_argument('--amp', type=float, default=None, help='覆盖固定峰值amp')
    ap.add_argument('--profile', default=None, help='使用自学习档案名')
    ap.add_argument('--learn-band', action='store_true', help='记录带内逐孔lift,更新档案(无对照组也可学逐孔)')
    ap.add_argument('--no-screen', action='store_true', help='只修齿孔带,不动成像区')
    ap.add_argument('--probe', action='store_true', help='只探测边界/孔位,不生成')
    ap.add_argument('--preview', action='store_true', help='额外导出交界预览图')
    ap.add_argument('--top', type=int, default=None)
    ap.add_argument('--bot', type=int, default=None)
    args = ap.parse_args()

    profile = load_profile(args.profile) if args.profile else None
    if args.profile and profile is None:
        print(f'!! 档案 {args.profile} 不存在, 回退固定标定。先运行 deglare_learn.py init/seed')

    p = args.input
    if os.path.isdir(p):
        files = sorted(glob.glob(os.path.join(p,'*.tif')) + glob.glob(os.path.join(p,'*.tiff')) +
                       glob.glob(os.path.join(p,'*.png')) + glob.glob(os.path.join(p,'*.jpg')))
        default_out = os.path.join(p, 'after')
    else:
        files = [p]
        default_out = os.path.join(os.path.dirname(os.path.abspath(p)), 'after')
    args.out = args.out or default_out
    os.makedirs(args.out, exist_ok=True)

    dark = C.hex2bgr01(args.dark)
    mode = f'档案 {args.profile}' if profile else f'固定 amp={args.amp if args.amp is not None else AMP_CAL}'
    print(f'输入 {len(files)} 张 | 底色 #{args.dark} | {mode} | strength={args.strength}')
    for f in files:
        print(f'\n[{os.path.basename(f)}]')
        try:
            process(f, args, dark, profile)
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f'  !! 失败: {e}')
    print('\ndone')

if __name__=='__main__': main()
