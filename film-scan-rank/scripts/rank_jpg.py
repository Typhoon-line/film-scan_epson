# -*- coding: utf-8 -*-
"""V700test 底片扫描排名 —— 统一命令入口。

由 AI 按用户自然语言翻译后调用本脚本:

  1) 范围重算排名:  rank_jpg.py --range START END
     -> 重新计算 [START, END] 区间内各组(含 A 基线, A 自动纳入计算),
        输出该区间的排名。例: --range A S

  2) 加测并入:      rank_jpg.py --add K1 K2 ...
     -> 只计算指定组(如 U V), 并入已有 rank_jpg.json(其余组不重算),
        重新排名后输出全量排名。例: --add U V

  3) 全量重算(默认): rank_jpg.py
     -> 重算注册表里全部已知组。

并行: OpenCV SIFT/findHomography 受 GIL 限制(实测多线程零加速), 故用多进程(每进程独立 GIL)。
      可用环境变量 V700_WORKERS=4 覆盖默认 6 个 worker。

输出: rank_jpg.md / .json / rank_jpg_vchart.png(均在脚本同目录)。
"""
import os, json, argparse, datetime, concurrent.futures as cf
import numpy as np
import cv2
import rank_core as fr
import rank_core as V
from PIL import Image, ImageDraw, ImageFont

FONT = r'C:/Windows/Fonts/msyh.ttc'

# ---- 配置 ----
MAX_WORKERS = int(os.environ.get('V700_WORKERS', '6'))  # 默认 6; V700_WORKERS=N 可做对比
OUTDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = V.ROOT
N = 6
OLD_JSON = os.path.join(OUTDIR, 'rank_jpg.json')
CHART = os.path.join(OUTDIR, 'rank_jpg_vchart.png')
MD = os.path.join(OUTDIR, 'rank_jpg.md')

# ---- 权重(单一来源, 改这里即可; 报告文字自动同步) ----
W_EDGE, W_GAMUT, W_SPAN = 0.70, 0.15, 0.15
W_PHYS_EDGE = W_EDGE / (W_EDGE + W_SPAN)   # 物理分(异常检测)只用锐度+跨度, 两者内部再归一
W_PHYS_SPAN = W_SPAN / (W_EDGE + W_SPAN)

# 显式注册表(主列表)。其余字母若 V700test 下存在 "{单字母}_*" 文件夹会自动并入(见 discover_folders)。
GROUPS = [
    ('A', 'A_原玻璃_原片夹_0'),
    ('B', 'B_原玻璃_原片夹垫高_1.0'),
    ('C', 'C_原玻璃_无片夹_0'),
    ('D', 'D_AR玻璃_原片夹_0'),
    ('E', 'E_AR玻璃_原片夹垫高_1.0'),
    ('F', 'F_AR玻璃_超薄片夹_0'),
    ('G', 'G_AR玻璃改_超薄片夹_0'),
    ('H', 'H_AR玻璃改_超薄片夹_0.25'),
    ('I', 'I_AR玻璃改_超薄片夹_0.5'),
    ('J', 'J_AR玻璃改_超薄片夹_0.75'),
    ('K', 'K_AR玻璃改_超薄片夹_1.0'),
    ('L', 'L_AR玻璃改_超薄片夹_1.25'),
    ('M', 'M_AR玻璃改_超薄片夹_1.5'),
    ('N', 'N_AR玻璃改_超薄片夹_1.75'),
    ('O', 'O_AR玻璃改_超薄片夹_2.0'),
    ('P', 'P_AR玻璃改_超薄片夹_2.25'),
    ('Q', 'Q_AR玻璃改_超薄片夹_2.5'),
    ('R', 'R_AR玻璃改_超薄片夹_2.75'),
    ('S', 'S_AR玻璃改_超薄片夹_3.0'),
    ('T', 'T_AR玻璃改_超薄片夹_2.625'),
    ('U', 'U_AR玻璃改_超薄片夹_2.375'),
]
FOLDER = dict(GROUPS)


def discover_folders():
    """把 V700test 下符合 {单字母}_* 的文件夹自动并入注册表, 便于加测新组无需改代码。
    返回新发现的字母列表。"""
    extra = []
    try:
        for name in os.listdir(ROOT):
            full = os.path.join(ROOT, name)
            if not os.path.isdir(full) or '_' not in name:
                continue
            letter = name.split('_', 1)[0]
            if len(letter) == 1 and letter.isalpha():
                u = letter.upper()
                if u not in FOLDER:
                    FOLDER[u] = name
                    extra.append(u)
    except FileNotFoundError:
        pass
    return extra


# ---------- 计算核心 ----------
def _compute_cell(payload):
    """单帧(格)完整计算, 可在子进程里独立运行(无共享可变状态)。
    payload = (k, group_frames, active); 返回 (k, cell_dict, detail_lines)。"""
    k, group_frames, active = payload
    paths = {key: group_frames[key][k - 1] for key in active}
    ref = fr.imread_any(paths['A'])
    ref_crop = V.center_crop(ref)
    ref_edge = fr.edge_sharpness(fr.gray(ref_crop))
    ref_gamut = fr.gamut_volume(ref_crop)
    ref_span, _, _ = fr.span_metrics(ref_crop)

    cell = {}
    detail = [f"格{k}: (计算中...)"]
    for key in active:
        if key == 'A':
            info = dict(edge=ref_edge, gamut=ref_gamut, span=ref_span,
                        pearson=1.0, inliers='-', op='identity', angle=0.0)
        else:
            cand = fr.imread_any(paths[key])
            info, cf_crop, ca = V.process_cand(ref, cand)
            del cand
        info = dict(info)
        if not (np.isfinite(ref_edge) and ref_edge > 0 and np.isfinite(ref_gamut)
                and ref_gamut > 0 and np.isfinite(ref_span) and ref_span > 0):
            raise ValueError(f"第{k}格参考基线非有限或<=0: edge={ref_edge} gamut={ref_gamut} span={ref_span}")
        if not all(np.isfinite(info[t]) for t in ('edge', 'gamut', 'span')):
            raise ValueError(f"第{k}格 {key} 指标含非有限值")
        info['edge_norm'] = info['edge'] / ref_edge * 100
        info['gamut_norm'] = info['gamut'] / ref_gamut * 100
        info['span_norm'] = info['span'] / ref_span * 100
        info['total_norm'] = (W_EDGE * info['edge_norm']
                              + W_GAMUT * info['gamut_norm']
                              + W_SPAN * info['span_norm'])
        info['physical_norm'] = (W_PHYS_EDGE * info['edge_norm']
                                 + W_PHYS_SPAN * info['span_norm'])
        cell[key] = info

    detail = [f"格{k}: " + "  ".join(f"{key}={cell[key]['total_norm']:.2f}" for key in active)]
    for key in active:
        if key != 'A':
            detail.append(f"   格{k} {key}: total={cell[key]['total_norm']:.2f} "
                          f"inl={cell[key]['inliers']} p={cell[key]['pearson']:.3f} {cell[key]['op']}")
    return k, cell, detail


def load_latest_n(folder, n=N):
    d = os.path.join(ROOT, folder)
    if not os.path.isdir(d):
        print(f"[跳过] 文件夹不存在: {d}")
        return []
    fs = sorted(f for f in os.listdir(d)
                if f.lower().endswith(fr.IMG_EXT) and f.lower().startswith('img'))
    picked = list(reversed(fs))[:n]
    picked.sort()
    return [os.path.join(d, f) for f in picked]


def compute_active(active):
    """加载 active 各组最新 N 张, 多进程算完, 返回 (per_cell, active_实际参与)。"""
    group_frames = {}
    for key in active:
        folder = FOLDER[key]
        paths = load_latest_n(folder)
        if len(paths) < N:
            print(f"[跳过] {folder} 仅 {len(paths)} 帧 < {N}")
            continue
        group_frames[key] = paths
        print(f"[{folder}] 取 {N} 张: " + ", ".join(os.path.basename(p) for p in paths))
    active = [k for k in active if k in group_frames]
    if 'A' not in active:
        raise SystemExit("基线 A 缺失, 无法计算")
    payloads = [(k, group_frames, active) for k in range(1, N + 1)]
    with cf.ProcessPoolExecutor(max_workers=min(MAX_WORKERS, os.cpu_count() or MAX_WORKERS)) as ex:
        computed = list(ex.map(_compute_cell, payloads))
    per_cell = {}
    for k, cell, detail in computed:
        for line in detail:
            print(line)
        per_cell[str(k)] = {key: cell[key] for key in active}
    return per_cell, active


def aggregate(per_cell, active):
    avg = {}
    for key in active:
        en = [per_cell[str(k)][key]['edge_norm'] for k in range(1, N + 1)]
        gn = [per_cell[str(k)][key]['gamut_norm'] for k in range(1, N + 1)]
        sn = [per_cell[str(k)][key]['span_norm'] for k in range(1, N + 1)]
        tn = [per_cell[str(k)][key]['total_norm'] for k in range(1, N + 1)]
        avg[key] = dict(edge=float(np.mean(en)), gamut=float(np.mean(gn)), span=float(np.mean(sn)),
                        total=float(np.mean(tn)),
                        edge_std=float(np.std(en)), gamut_std=float(np.std(gn)),
                        span_std=float(np.std(sn)), total_std=float(np.std(tn)))
    return avg


# ---------- 两种模式 ----------
def run_range(start, end):
    s, e = start.upper(), end.upper()
    range_keys = sorted(k for k in FOLDER if s <= k <= e)
    if not range_keys:
        raise SystemExit(f"范围 {s}-{e} 内无已知组")
    active = list(range_keys)
    if 'A' not in active:
        active = ['A'] + active  # A 作基线必须参与计算
    print(f"[范围重算] {s}-{e}  ->  计算组: {active}")
    per_cell, active = compute_active(active)
    avg = aggregate(per_cell, active)
    mode = f"范围重算 {s}-{e}（A 为基线, 全部重新计算）"
    footnote = "本批为指定范围内全部重新计算, 未复用旧排名 JSON。"
    # 只把真正算出结果的组交给报告; 帧数不足被跳过的组若混进 display_keys 会让
    # make_vchart 的 avg[k] 抛 KeyError。
    build_report(avg, per_cell, active, mode, footnote)
    return avg, active


def run_add(keys):
    keys = [k.upper() for k in keys]
    if not os.path.exists(OLD_JSON):
        print("[无旧 JSON] 退化为全量计算: " + " ".join(keys))
        active = ['A'] + [k for k in keys if k != 'A']
        per_cell, active = compute_active(active)
        avg = aggregate(per_cell, active)
        mode = f"加测并入 {'/'.join(keys)}（无旧数据, 退化为全量）"
        footnote = "本批全部重新计算(无旧 JSON 可并入)。"
        build_report(avg, per_cell, active, mode, footnote)
        return avg, active

    old = json.load(open(OLD_JSON, encoding='utf-8'))
    old_avg = old['average']
    old_per = old['per_cell']
    new_keys = [k for k in keys if k not in old_avg]
    if not new_keys:
        print("[提示] 请求组均已在旧数据中, 按重算它们处理以保持一致")
        new_keys = list(keys)
    print(f"[加测并入] 新算: {new_keys}  | 沿用旧数据组: "
          f"{[k for k in old_avg if k not in new_keys]}")
    active_small = ['A'] + [k for k in new_keys if k != 'A']
    per_cell_new, _ = compute_active(active_small)

    merged_per = {}
    for k in range(1, N + 1):
        merged_per[str(k)] = dict(old_per[str(k)])       # 旧组原样保留
        for nk in new_keys:
            merged_per[str(k)][nk] = per_cell_new[str(k)][nk]
    merged_avg = dict(old_avg)
    for nk in new_keys:
        en = [per_cell_new[str(k)][nk]['edge_norm'] for k in range(1, N + 1)]
        gn = [per_cell_new[str(k)][nk]['gamut_norm'] for k in range(1, N + 1)]
        sn = [per_cell_new[str(k)][nk]['span_norm'] for k in range(1, N + 1)]
        tn = [per_cell_new[str(k)][nk]['total_norm'] for k in range(1, N + 1)]
        merged_avg[nk] = dict(edge=float(np.mean(en)), gamut=float(np.mean(gn)), span=float(np.mean(sn)),
                              total=float(np.mean(tn)),
                              edge_std=float(np.std(en)), gamut_std=float(np.std(gn)),
                              span_std=float(np.std(sn)), total_std=float(np.std(tn)))
    all_keys = list(merged_avg.keys())
    mode = f"加测并入 {'/'.join(new_keys)}（其余 {len(all_keys) - len(new_keys)} 组沿用旧数据不重算）"
    footnote = (f"本批 = 原有数据(前次) + 单独并入 {','.join(new_keys)}（本次）。"
                "其它组数值未重算, 与原结果一致。")
    build_report(merged_avg, merged_per, all_keys, mode, footnote)
    return merged_avg, all_keys


# ---------- 每组第一帧联系表(核对调转方向算法) ----------
def make_firstframe_sheet(per_cell, display_keys):
    """每组第一帧联系表: 左=原始扫描方向, 右=算法校正后方向(op 直接取自已算指标),
    低质量拼合, 用于核对调转方向算法是否运算正确。

    op 来源: per_cell['1'][key]['op'](=排名时 process_cand 算出的 8 向朝向,
    含 180°-partner 校正)。第一帧 = k=1 帧, 与排名所用第 1 格一致。
    """
    L = 332            # 缩略最长边
    TILE_W, TILE_H = 460, 358
    CAP = 26
    GAP = 8
    Q = 80             # 拼合 jpg 质量(降低质量)
    out = os.path.join(OUTDIR, 'rank_jpg_orient.jpg')

    def resize_longest(im, L):
        h, w = im.shape[:2]
        if max(h, w) <= L:
            return im
        s = L / max(h, w)
        return cv2.resize(im, (int(round(w * s)), int(round(h * s))))

    def tile(im, caption):
        t = Image.new('RGB', (TILE_W, TILE_H), (255, 255, 255))
        d = ImageDraw.Draw(t)
        d.rectangle([0, 0, TILE_W, CAP], fill=(225, 225, 225))
        try:
            f = ImageFont.truetype(FONT, 16)
        except Exception:
            f = ImageFont.load_default()
        d.text((6, 4), caption, fill=(20, 20, 20), font=f)
        thumb = Image.fromarray(cv2.cvtColor(resize_longest(im, L), cv2.COLOR_BGR2RGB))
        tw, th = thumb.size
        x = (TILE_W - tw) // 2
        y = CAP + (TILE_H - CAP - th) // 2
        t.paste(thumb, (x, y))
        return t

    # A 永远作为参考首行(原向), 其余按 display_keys
    sheet_keys = sorted(set(['A']) | set(display_keys))
    cells = []
    for key in sheet_keys:
        folder = FOLDER.get(key)
        if not folder:
            continue
        try:
            fpath = load_latest_n(folder)[0]   # 第一帧(k=1)
            raw = fr.imread_safe(fpath)
        except Exception as e:
            print(f"[联系表] {key} 读取失败, 跳过: {e}")
            continue
        if raw is None:
            continue
        op = per_cell.get('1', {}).get(key, {}).get('op', 'identity')
        if op not in fr.OPS:
            op = 'identity'
        oriented = fr.OPS[op](raw)
        cells.append((key, op, raw, oriented))
        print(f"[联系表] {key} 第一帧 op={op}")

    n = len(cells)
    if n == 0:
        print("[联系表] 无合格帧, 跳过"); return
    header_h = 40
    sheet = Image.new('RGB', (2 * TILE_W + GAP, header_h + n * (TILE_H + GAP)), (255, 255, 255))
    hd = ImageDraw.Draw(sheet)
    try:
        hf = ImageFont.truetype(FONT, 16)
    except Exception:
        hf = ImageFont.load_default()
    hd.text((6, 10), "左=原始扫描方向   右=算法校正后方向(op)   参考A=原向",
            fill=(20, 20, 20), font=hf)
    for i, (key, op, raw, oriented) in enumerate(cells):
        y = header_h + i * (TILE_H + GAP)
        sheet.paste(tile(raw, f"{key} 原始"), (0, y))
        sheet.paste(tile(oriented, f"{key} {V.OP_DISPLAY.get(op, op)}"), (TILE_W + GAP, y))
    sheet.save(out, 'JPEG', quality=Q)
    print(f"[联系表] 已生成: {out}  ({sheet.size[0]}x{sheet.size[1]}, {n} 组)")


# ---------- 报告生成 ----------
def build_report(avg, per_cell, display_keys, mode_label, footnote):
    # 防御: 剔除因帧数不足 / 文件夹缺失被 compute_active 跳过、因而 avg 中没有的组
    missing = [k for k in display_keys if k not in avg]
    if missing:
        print(f"[报告] 以下组无计算结果, 已从报告剔除: {missing}")
    display_keys = sorted(k for k in display_keys if k in avg)  # 按 A..U 顺序(不随总分排序)
    if not display_keys:
        raise SystemExit("无任何有效组, 不生成报告")
    ranked = sorted(display_keys, key=lambda k: -avg[k]['total'])
    V.make_vchart(avg, display_keys, CHART)
    make_firstframe_sheet(per_cell, display_keys)   # 每组第一帧联系表(核对调转方向)

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    L = []
    L.append('# V700test 平均成绩排名（底片扫描排名 · 每格同帧配对 · A 基准=100）')
    L.append('')
    L.append(f'_生成: {now} | **{mode_label}** | 每组=6帧, 第k格跨组同帧 | 基线: A_原玻璃_原片夹_0 = 100 | 共 {len(display_keys)} 组_')
    L.append('')
    L.append('![V 型焦点模型图: 总分 vs 文件夹(甜点区/最好成绩)](rank_jpg_vchart.png)')
    L.append('')
    L.append('**指标说明**')
    L.append('- **锐度（edge_sharp）**：中心裁窗内「梯度 ÷ 局部对比度」的中位数——除以每条边自身的明暗差，故与画面内容、整体曝光无关。越高越清晰。'
             f'权重 {W_EDGE:.2f}。')
    L.append(f'- **色域（gamut体积）**：裁窗内 Lab 色彩直方图占据的格子数，反映色彩信息量与细节丰富度。权重 {W_GAMUT:.2f}。')
    L.append(f'- **跨度（span）**：裁窗内像素明暗分布的跨度（动态范围代理），反映层次。权重 {W_SPAN:.2f}。')
    L.append(f'- 总分 = {W_EDGE:.2f}×锐度 + {W_GAMUT:.2f}×色域 + {W_SPAN:.2f}×跨度，均以 A 为基准归一（A=100）。')
    L.append('- **扫描一致性** = 0.65×锐度std + 0.35×跨度std（不含色域std，因色域主要受每帧画面内容影响、与扫描物理一致性无关）。值越小=6 帧物理扫描越一致；仅作诊断，不参与排名。')
    L.append('')
    L.append('## 总分排名（6 格平均, A=100 基准）')
    L.append('')
    L.append('| 名次 | 组 | 锐度 | 色域 | 跨度 | 总分 | 扫描一致性 |')
    L.append('|---|---|---|---|---|---|---|')
    for i, key in enumerate(ranked):
        a = avg[key]
        cfg = 0.65 * a['edge_std'] + 0.35 * a['span_std']
        L.append(f"| #{i+1} | {key} ({FOLDER.get(key, '')}) | {a['edge']:.2f} | "
                 f"{a['gamut']:.2f} | {a['span']:.2f} | "
                 f"**{a['total']:.2f}** | {cfg:.2f} |")
    L.append('')
    L.append('## 每格明细（total_norm, A 每格=100）')
    L.append('')
    L.append('| 格 | A | 其余各组 total | 同帧校验(内点<30 或 Pearson<0.85) |')
    L.append('|---|---|---|---|')
    for k in range(1, N + 1):
        c = per_cell[str(k)]
        others = ", ".join(f"{key}={c[key]['total_norm']:.2f}" for key in display_keys if key != 'A')
        warns = [f"{key}⚠" for key in display_keys if key != 'A'
                 and c[key]['inliers'] != '-' and (c[key]['inliers'] < 30 or c[key]['pearson'] < 0.85)]
        L.append(f"| {k} | {c['A']['total_norm']:.2f} | {others} | {', '.join(warns) or 'OK'} |")
    L.append('')
    L.append('> 方法: 每格以 A_k 为参考, 各候选组经 8 向枚举 + 单应对齐到 A 方向后, 在原图裁窗(6000x4000, 无 warp)上算 edge/gamut/span; A 恒为基线=100, 6 格取平均。' + footnote)
    L.append('> 同帧校验: SIFT 内点应 >30, 裁窗 Pearson 应 >0.85; 出现 ⚠ 表示该格对齐质量偏低, 但其指标仍在正确裁出的原图像素上算, 不代表错误。横/竖构图差异由 8 向枚举自动吸收。')
    L.append('')
    L.append('## 原始指标（每格 各绝对值）')
    L.append('')
    L.append('| 格 | 组 | 锐度成绩 | 色域成绩 | 跨度成绩 | 朝向 |')
    L.append('|---|---|---|---|---|---|')
    for k in range(1, N + 1):
        for j, key in enumerate(display_keys):
            c = per_cell[str(k)][key]
            kcell = k if j == 0 else ''
            L.append(f"| {kcell} | {key} | {c['edge']:.4f} | {c['gamut']} | {c['span']:.1f} | {V.OP_DISPLAY.get(c['op'], c['op'])} |")
    L.append('')
    L.append('## 异常提示（单张低分 → 待确认后再做检测）')
    L.append('')
    L.append(f'异常判据 = **物理分 = {W_PHYS_EDGE:.2f}×锐度 + {W_PHYS_SPAN:.2f}×跨度**（即总分权重里锐度:跨度 = {W_EDGE:.2f}:{W_SPAN:.2f} 的内部归一，不含色域），'
             '对每组 6 格做统计，仅标记低于该组均值 **−8% 以上**（A 基准=100）的单张。'
             '色域（gamut）由每帧底片内容主导、非扫描物理量，故不参与异常触发——避免把"某格负片色彩偏少"这类正常内容差异误标为故障。'
             '锐度口径为「梯度 ÷ 局部对比度」中位数（相对每条边自身明暗差归一），对画面内容与整体曝光不敏感，故此类偏离更值得怀疑是真实光学问题。'
             '**归因判别**：若某一帧在**多数配置**中一致偏低 → 属该底片格自身内容/拍摄端偏软，非扫描故障；若仅**个别配置**的某帧偏低 → 才指向该次扫描的对焦波动、牛顿环、摩尔纹或灰尘脏污。'
             '**需用户确认后才针对单帧做定向检测**。')
    L.append('')
    L.append('| 组 | 异常格 | 物理分(锐+跨) | 组物理均值 | 偏离 | 提示 |')
    L.append('|---|---|---|---|---|---|')
    any_flag = False
    for key in display_keys:
        phys = [per_cell[str(kk)][key]['physical_norm'] for kk in range(1, N + 1)]
        m = float(np.mean(phys))
        for k in range(1, N + 1):
            v = per_cell[str(k)][key]['physical_norm']
            dev = (v - m) / m * 100 if m else 0.0
            if dev <= -8.0:
                any_flag = True
                L.append(f"| {key} | {k} | {v:.2f} | {m:.2f} | {dev:+.1f}% | 建议确认是否做异常检测(牛顿环/摩尔纹/灰尘/对焦) |")
    if not any_flag:
        L.append('| — | — | — | — | — | 本批无单张显著低分 |')
    L.append('')
    L.append('> 异常单张已在表中标出，可回到原图对应格放大复核（牛顿环 / 摩尔纹 / 灰尘脏污）。')
    L.append('')

    with open(MD, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    with open(OLD_JSON, 'w', encoding='utf-8') as f:
        json.dump({'average': avg, 'ranking': ranked, 'per_cell': per_cell},
                  f, ensure_ascii=False, indent=2)

    print(f"\n排名: " + " > ".join(f"{k}={avg[k]['total']:.2f}" for k in ranked))
    print(f"报告: {MD}\nJSON: {OLD_JSON}\n图表: {CHART}")


# ---------- 入口 ----------
def main():
    ap = argparse.ArgumentParser(description='V700test 底片扫描排名')
    mg = ap.add_mutually_exclusive_group()
    mg.add_argument('--range', nargs=2, metavar=('START', 'END'),
                    help='范围重算并排名, 如 --range A S')
    mg.add_argument('--add', nargs='+', metavar='KEY',
                    help='加测指定组并入已有数据, 如 --add U V')
    mg.add_argument('--list', action='store_true', help='列出已知组(文件夹)后退出')
    args = ap.parse_args()

    extra = discover_folders()
    if extra:
        print(f"[自动发现新文件夹] {extra} -> 已并入注册表")

    if args.list:
        print("已知组(字母=文件夹):")
        for k in sorted(FOLDER):
            print(f"  {k}  {FOLDER[k]}")
        return

    if args.range:
        run_range(*args.range)
    elif args.add:
        run_add(args.add)
    else:
        # 全量
        print("[全量重算] 全部已知组")
        active = list(FOLDER.keys())
        per_cell, active = compute_active(active)
        avg = aggregate(per_cell, active)
        mode = "全部重新计算（不复用旧 JSON）"
        footnote = "本批全部重新计算, 未复用旧排名 JSON。"
        build_report(avg, per_cell, active, mode, footnote)


if __name__ == '__main__':
    main()
