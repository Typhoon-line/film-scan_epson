# -*- coding: utf-8 -*-
"""
修复效果检测 (只读, 不学习、不入库)
=====================================
对已修复图 vs 无齿孔对照, 测孔位对齐的残余光晕:
  before = 原图孔下额外抬升 (光晕强度真值)
  after  = 修复图孔下额外抬升 (残余)
  reduction = 1 - after/before

【重要职责边界 —— 2026-11-25 修正】
本脚本只做"检测修改效果", 绝不做以下两件事:
  1. 不把修复图( *_deglared.tif )当样本 —— 它的光晕已被人为改过, 不是客观观测
  2. 不把 before 重复写入样本池 —— before 就是 seed/add 阶段的同一条观测,
     重复写入只是自我循环, 会让样本数虚增而没有任何新信息

样本池只由 deglare_learn.py 的 seed/add 写入, 数据源必须是"未修改的原扫描图 + 其无孔对照"。
新扫描要学习: 用 deglare_learn.py add --with 新图 --without 新对照。

CLI:
  python deglare_verify.py --name NAME --orig A.tif --fixed A_deglared.tif --ctl B.tif
  python deglare_verify.py --name NAME --batch pairs.json
  python deglare_verify.py --name NAME --report
"""
import os, json, argparse, datetime, numpy as np
import deglare_core as C

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, 'deglare_profiles.json')
MIN_SNR = 0.4

def _now(): return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def load_db():
    return json.load(open(DB_PATH,encoding='utf-8')) if os.path.exists(DB_PATH) else {'version':1,'profiles':{}}

def save_db(db):
    tmp=DB_PATH+'.tmp'; json.dump(db,open(tmp,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
    os.replace(tmp,DB_PATH)

def measure_pair(orig_path, fixed_path, ctl_path):
    o = C.load(orig_path); f = C.load(fixed_path); c = C.load(ctl_path)
    o01,_,_ = C.to_unchanged_scale(o); f01,_,_ = C.to_unchanged_scale(f); c01,_,_ = C.to_unchanged_scale(c)
    centers,hy,holes = C.hole_centers(o01)
    dx,dy,inl,ng = C.sift_shift(o,c)
    before = C.pair_delta(o01,c01,dx,dy,centers)
    after  = C.pair_delta(f01,c01,dx,dy,centers)
    return dict(before=before['delta'], after=after['delta'], snr=before['snr'],
                dx=dx, dy=dy, inliers=inl, n_holes=len(centers))

def record_check(db, name, results, paths):
    """只写 verify 检测记录 (去重), 绝不碰 samples / amp_center"""
    p=db['profiles'][name]
    existing={(x.get('fixed'), x.get('amp_center_used')) for x in p.get('verify',[])}
    added=0
    for r,f in zip(results,paths):
        # 同一修复图 + 同一参数已测过则跳过
        key=(os.path.basename(f), round(float(p['amp_center']),6))
        if key in existing: continue
        red = 1-float(r['after'])/float(r['before']) if float(r['before'])>1e-6 else 0.0
        p.setdefault('verify',[]).append(dict(
            orig=os.path.basename(r['orig']), fixed=os.path.basename(f),
            before=round(float(r['before']),6), after=round(float(r['after']),6),
            reduction=round(red,4), snr=round(float(r['snr']),3),
            amp_center_used=round(float(p['amp_center']),6),
            date=_now(), note='仅效果检测,不入库'))
        existing.add(key); added+=1
    return dict(recorded=added, samples_unchanged=len(p['samples']),
                amp_center_unchanged=round(float(p['amp_center']),6))

def print_report(db,name):
    p=db['profiles'][name]
    v=p.get('verify',[])
    print(f'\n=== {name} 效果检测 (n={len(v)}, 仅记录不学习) ===')
    print(f'样本池 n={p["n_amp"]} (全部为独立原扫描) | amp_center={p["amp_center"]:.6f} 未被检测改动')
    if not v:
        print('  尚无检测记录'); return
    for x in v:
        tag = '低信噪(残余符号不可靠)' if x['snr']<1.5 else '可信'
        red = max(0.0, min(1.0, x['reduction']))
        print(f'  {x.get("fixed","?")}: before={x["before"]:+.6f} after={x["after"]:+.6f} '
              f'消除≈{red*100:.0f}% snr={x["snr"]:.2f}[{tag}] 参数={x.get("amp_center_used",0):.6f}')
    good=[x for x in v if x['snr']>=1.5 and 'before' in x]
    reds=[max(0.0,min(1.0,x['reduction'])) for x in good if x['before']>1e-6]
    if reds:
        print(f'  高信噪样本(n={len(reds)}) 消除率 中位={np.median(reds)*100:.1f}%')
    else:
        print('  无高信噪样本, 不做消除率统计')

def main():
    ap=argparse.ArgumentParser(description='光晕修复效果检测 (只读, 不学习)')
    ap.add_argument('--name',required=True)
    ap.add_argument('--orig'); ap.add_argument('--fixed'); ap.add_argument('--ctl')
    ap.add_argument('--batch',help='JSON: [{orig,fixed,ctl},...]')
    ap.add_argument('--report',action='store_true')
    args=ap.parse_args()
    db=load_db()
    if args.name not in db['profiles']:
        print('档案不存在:',args.name); return
    if args.report:
        print_report(db,args.name); return
    items=[]
    if args.batch:
        items=json.load(open(args.batch,encoding='utf-8'))
    elif args.orig and args.fixed and args.ctl:
        items=[dict(orig=args.orig,fixed=args.fixed,ctl=args.ctl)]
    else:
        ap.error('需 --orig/--fixed/--ctl 或 --batch 或 --report')
    results=[]; paths=[]
    for it in items:
        print(f'\n[{os.path.basename(it["fixed"])}]')
        r=measure_pair(it['orig'],it['fixed'],it['ctl'])
        r['orig']=it['orig']
        print(f'  before={r["before"]:+.6f} after={r["after"]:+.6f} snr={r["snr"]:.2f} '
              f'd=({r["dx"]:+d},{r["dy"]:+d}) inl={r["inliers"]}')
        results.append(r); paths.append(it['fixed'])
    res=record_check(db,args.name,results,paths)
    print('\n检测记录:',json.dumps(res,ensure_ascii=False))
    save_db(db)
    print_report(db,args.name)

if __name__=='__main__': main()
