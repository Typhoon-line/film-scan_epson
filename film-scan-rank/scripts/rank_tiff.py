# -*- coding: utf-8 -*-
"""V700test 底片扫描排名 —— TIFF 专用测试版。

与 rank_jpg.py 的区别：
  1) 只读取 .tif / .tiff 文件，忽略同文件夹内的旧 .jpg（避免混合 8bit JPEG 与 16bit TIFF）。
  2) 只对指定组（默认 M / O / Q）的 16bit TIFF 重扫数据做排名。
  3) 基线 = M（TIFF），而非历史 A（8bit JPEG）。参与组同为 16bit sRGB TIFF、
     USM=无、AE=关，故各组之间横向可比；但本榜分数与历史 A=100 主榜不可直接比较
     （基线不同、位深不同）。
  4) 输出到独立文件（rank_tiff.json / .md / rank_tiff_vchart.png），与 JPG 管线的 rank_jpg.* 分开，互不覆盖。

用法（全量 / 加测 两种模式共用一个文件）：
  python rank_tiff.py                       # 全量重算所有已登记组
  python rank_tiff.py --groups M O Q        # 仅重算指定组
  python rank_tiff.py --add ZI ZI_AR玻璃改_超薄片夹_3.5_M10
                                                       # 增量加测单组, 合并进既有结果(不重算其它组)
  python rank_tiff.py --list                # 列出已登记的组代号与文件夹
"""
import os, json, argparse, datetime, concurrent.futures as cf
import numpy as np
import cv2
import rank_core as fr
import rank_core as V
from PIL import Image, ImageDraw, ImageFont

FONT = r'C:/Windows/Fonts/msyh.ttc'

# ---- 配置 ----
MAX_WORKERS = int(os.environ.get('V700_WORKERS', '6'))
OUTDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = V.ROOT
N = 6
IMG_EXT_TIFF = ('.tif', '.tiff')          # 仅 TIFF

# 权重（与主榜一致，单一来源）
W_EDGE, W_GAMUT, W_SPAN = 0.85, 0.15, 0.00
W_PHYS_EDGE = W_EDGE / (W_EDGE + W_SPAN)   # = 1.0（总分内锐度:跨度=0.85:0.00 归一）
W_PHYS_SPAN = W_SPAN / (W_EDGE + W_SPAN)   # = 0.0（跨度不计入总分）

# 默认参与组 + 基线。基线必须是 TIFF 组之一。
# 此表是「组代号 → 文件夹名」的主映射；--groups 只能引用此表中的键。
DEFAULT_GROUPS = [
    ('M', 'M_AR玻璃改_超薄片夹_1.5'),
    ('MM', 'M_AR玻璃改_超薄片夹_1.5_M10'),
    ('KM', 'K_AR玻璃改_超薄片夹_1.0_M10'),
    ('N', 'N_AR玻璃改_超薄片夹_1.75'),
    ('O', 'O_AR玻璃改_超薄片夹_2.0'),
    ('OM', 'O_AR玻璃改_超薄片夹_2.0_M10'),
    ('P', 'P_AR玻璃改_超薄片夹_2.25'),
    ('Q', 'Q_AR玻璃改_超薄片夹_2.5'),
    ('QM', 'Q_AR玻璃改_超薄片夹_2.5_M10'),
    ('R', 'R_AR玻璃改_超薄片夹_2.75'),
    ('T', 'T_AR玻璃改_超薄片夹_2.625'),
    ('U', 'U_AR玻璃改_超薄片夹_2.375'),
    ('V', 'V_AR玻璃改_超薄片夹_2_1.75'),
    ('W', 'W_AR玻璃改_超薄片夹_2_2.25'),
    ('X', 'X_AR玻璃改_超薄片夹_5mm_M10'),
    ('Y', 'Y_AR玻璃改_超薄片夹_4.5mm_M10'),
    ('Z', 'Z_AR玻璃改_超薄片夹_4mm_M10'),
    ('ZA', 'ZA_AR玻璃改_超薄片夹_3.5mm_M10'),
    ('ZB', 'ZB_AR玻璃改_超薄片夹_2_M10'),
    ('ZC', 'ZC_AR玻璃改_超薄片夹_2.5_M10'),
    ('ZD', 'ZD_AR玻璃改_超薄片夹_2.75_M10'),
    ('ZE', 'ZE_AR玻璃改_超薄片夹_3_M10'),
    ('ZF', 'ZF_AR玻璃改_超薄片夹_3.25_M10'),
    ('ZG', 'ZG_AR玻璃改_超薄片夹_3.25_M10_d'),
    ('ZH', 'ZH_AR玻璃改_超薄片夹_3.25_M10_1'),
]
BASELINE = 'M'
FOLDER = dict(DEFAULT_GROUPS)

# 配置性质说明（仅作报告脚注，不影响计算）。
# W 为斜楔: 一端 2.0mm、另一端 2.25mm，是 2.0→2.25mm 的连续梯度，
# 并非均匀 2.25mm 垫高，故其总分应理解为该段的平均值，不可与 P(均匀 2.25) 直接比对复现性。
CONFIG_NOTE = {
    'W': '斜楔 2.0→2.25mm（一端 2.0、另一端 2.25，连续梯度，非均匀 2.25mm）',
    'V': '斜楔 2.0→1.75mm（一端 2.0、另一端 1.75，连续梯度，非均匀 1.75mm）',
    'OM': 'O(2.0mm) 的 M10 工艺重扫变体（_M10 后缀，与原 O 同分辨率），几何同为均匀 2.0mm，无玻璃压；用于复现性/工艺对照',
    'QM': 'Q(2.5mm) 的 M10 工艺变体（_M10 后缀），几何同为均匀 2.5mm，用于复现性/工艺对照',
    'MM': 'M(1.5mm) 的 M10 工艺重扫变体（_M10 后缀，与原 M 同分辨率），几何同为均匀 1.5mm，用于复现性/工艺对照',
    'KM': 'K(1.0mm) 的 M10 工艺重扫变体（_M10 后缀），几何同为均匀 1.0mm，用于复现性/工艺对照（注：K 基座仅旧 JPEG、未做 TIFF，故 KM 以 M=100 为基准独立入榜）',
    'X': 'X(5.0mm) 的 M10 工艺变体（_M10 后缀），几何为均匀 5.0mm 高垫高。⚠实测 edge=102.53 / 总=101.63，反高于 M=100；但这**不能读成「5mm 比 1.5mm 好」**——X 扫于 11-05 加填充物(面板已修)后，M 基准扫于 10-18 搬家变形前，两者面板状态不同，且 5mm 远超 V700 景深±0.25mm 物理上必欠焦。真正可比需在同一面板状态下做 5mm vs 1.5mm 同批重扫。',
    'ZG': 'ZG(3.25mm) 的 M10 重扫变体（_d 后缀）。⚠**本组对齐质量差、分数偏低，谨慎使用**：6 帧方向共识失败（3/6，op=NONE，退原生中心裁窗），内点数偏低（5–15），总分 94.09。注意: 逐帧中心裁窗亮度 B/G/R 跳变与锐度单调下降在 M 基线、ZH 有效组上同样存在（是 6 帧对应标靶 6 个不同物理区域的内容/会话效应，非底片漂移），故不能据此判无效；真正的减分信号是**方向共识失败 + 内点过低**, 提示该批扫描对位/清晰度本身存疑。3.25mm 高度的可靠参考以 ZF=103.03 / ZH=103.30 两组一致结果为准。',
    'ZH': 'ZG 同高度(3.25mm)的 M10 重扫变体（_1 后缀），用于复现性对照。6 帧方向共识 6/6 identity、SIFT 内点 53–859、Pearson 0.94–0.99，对齐质量良好。逐帧曲线 109.93/107.55/105.19/102.23/99.11/95.77 与 ZF 几乎重合，总分 103.30 与 ZF=103.03 高度一致——**3.25mm 的 103 分档已由 ZF/ZH 两组独立复现**，可作为该高度的可靠参考。',
}

# 输出（独立后缀，避免覆盖 JPG 管线）
OLD_JSON = os.path.join(OUTDIR, 'rank_tiff.json')
CHART = os.path.join(OUTDIR, 'rank_tiff_vchart.png')
MD = os.path.join(OUTDIR, 'rank_tiff.md')
# 加测组持久登记表: --add 新增的组写进这里, 下次 --list / --groups / 全量都能带上。
# 该文件是用户本地数据, 已被 .gitignore 的 *.json 规则忽略, 不随仓库分发。
REGISTRY = os.path.join(OUTDIR, 'groups.json')


def load_registry():
    """把历史加测组并入 FOLDER, 返回本次新载入的组代号。"""
    if not os.path.exists(REGISTRY):
        return []
    added = []
    try:
        with open(REGISTRY, encoding='utf-8') as f:
            for k, v in json.load(f).items():
                if k not in FOLDER:
                    FOLDER[k] = v
                    added.append(k)
    except Exception as e:
        print(f"[警告] 登记表 {REGISTRY} 读取失败, 已忽略: {e}")
    return added


def save_registry():
    """把 DEFAULT_GROUPS 之外的组(即 --add 加测组)落盘, 供后续运行复用。"""
    builtin = dict(DEFAULT_GROUPS)
    extra = {k: v for k, v in FOLDER.items() if k not in builtin}
    try:
        with open(REGISTRY, 'w', encoding='utf-8') as f:
            json.dump(extra, f, ensure_ascii=False, indent=2)
        print(f"[登记表] 已保存 {len(extra)} 个加测组 -> {REGISTRY}")
    except Exception as e:
        print(f"[警告] 登记表写入失败(不影响本次结果): {e}")


def load_latest_n_tiff(folder, n=N):
    """只取 .tif / .tiff，忽略同目录的旧 .jpg。目录不存在返回 [],
    让 compute_active 走「跳过该组」而不是整个进程崩掉。"""
    d = os.path.join(ROOT, folder)
    if not os.path.isdir(d):
        print(f"[跳过] 文件夹不存在: {d}")
        return []
    fs = sorted(f for f in os.listdir(d)
                if f.lower().endswith(IMG_EXT_TIFF) and f.lower().startswith('img'))
    picked = list(reversed(fs))[:n]
    picked.sort()
    return [os.path.join(d, f) for f in picked]


# ---------- 计算核心 ----------
def _compute_cell(payload):
    """单格完整计算，可在子进程独立运行。payload=(k, group_frames, active)。"""
    k, group_frames, active, consensus = payload
    paths = {key: group_frames[key][k - 1] for key in active}
    ref = fr.imread_any(paths[BASELINE])
    ref_crop = V.center_crop(ref)
    ref_edge = fr.edge_sharpness(fr.gray(ref_crop))
    ref_gamut = fr.gamut_volume(ref_crop)
    ref_span, _, _ = fr.span_metrics(ref_crop)

    cell = {}
    for key in active:
        if key == BASELINE:
            info = dict(edge=ref_edge, gamut=ref_gamut, span=ref_span,
                        pearson=1.0, inliers='-', op='identity', angle=0.0)
        else:
            cand = fr.imread_any(paths[key])
            cg, cop = consensus.get(key, (0, None))
            info, cf_crop, ca = V.process_cand(ref, cand, consensus_op=cop,
                                               good_count=cg, use_consensus=True)
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
        if key != BASELINE:
            deg = ' deg' if cell[key].get('degenerate') else ''
            con = ' con' if cell[key].get('consensus') else ''
            detail.append(f"   格{k} {key}: total={cell[key]['total_norm']:.2f} "
                          f"inl={cell[key]['inliers']} p={cell[key]['pearson']:.3f} {cell[key]['op']}{deg}{con}")
    return k, cell, detail


def orientation_pre_pass(group_frames, active):
    """组内共识方向预扫描: 对每个组每帧跑方向验证(SIFT 取向+单应内点),
    统计方向验证通过(内点>=30)的帧数 good, 取通过帧朝向的众数作为共识方向 consensus_op。
    返回 consensus: dict[group] = (good_count, consensus_op)。"""
    from collections import Counter
    consensus = {}
    for key in active:
        if key == BASELINE:
            consensus[key] = (N, 'identity')
            continue
        ops = []
        good = 0
        for k in range(1, N + 1):
            ref = fr.imread_any(group_frames[BASELINE][k - 1])
            cand = fr.imread_any(group_frames[key][k - 1])
            if ref is None or cand is None:
                continue
            det = V.detect_orientation(ref, cand)
            if det['inl_n'] >= 30:
                good += 1
                ops.append(det['best_op'])
        cop = None
        if ops:
            cnt = Counter(ops)
            op0, n0 = cnt.most_common(1)[0]
            # 需明确多数: 通过帧中占比严格过半(排除 3-3 平局)且不少于 4 张,
            # 否则视为无共识 —— SIFT 对暗/条纹/模糊内容可能取向分歧, 强行取多数反而出错。
            if n0 >= 4 and n0 > len(ops) // 2:
                cop = op0
        consensus[key] = (good, cop)
        tag = cop if cop else 'NONE(取向分歧, 退回原生中心裁窗)'
        print(f"[consensus] {key}: dir-ok {good}/{N}, op={tag}")
    return consensus
def compute_active(active):
    group_frames = {}
    for key in active:
        folder = FOLDER[key]
        paths = load_latest_n_tiff(folder)
        if len(paths) < N:
            print(f"[跳过] {folder} 仅 {len(paths)} 帧TIFF < {N}")
            continue
        group_frames[key] = paths
        print(f"[{folder}] 取 {N} 张TIFF: " + ", ".join(os.path.basename(p) for p in paths))
    active = [k for k in active if k in group_frames]
    # 基线检查必须排在 orientation_pre_pass 之前, 否则基线缺失时会在预扫描里抛 KeyError
    if BASELINE not in active:
        raise SystemExit(f"基线 {BASELINE}(TIFF) 缺失, 无法计算")
    consensus = orientation_pre_pass(group_frames, active)
    payloads = [(k, group_frames, active, consensus) for k in range(1, N + 1)]
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


# ---------- 每组第一帧联系表(核对调转方向算法) ----------
def make_firstframe_sheet(per_cell, display_keys):
    L = 332
    TILE_W, TILE_H = 460, 358
    CAP = 26
    GAP = 8
    Q = 80
    out = os.path.join(OUTDIR, 'rank_tiff_orient.jpg')

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
        disp = resize_longest(im, L)
        if disp.dtype != np.uint8:                      # 16bit TIFF -> 8bit 供缩略图显示
            disp = cv2.convertScaleAbs(disp, alpha=1.0 / 257.0)
        thumb = Image.fromarray(cv2.cvtColor(disp, cv2.COLOR_BGR2RGB))
        tw, th = thumb.size
        x = (TILE_W - tw) // 2
        y = CAP + (TILE_H - CAP - th) // 2
        t.paste(thumb, (x, y))
        return t

    sheet_keys = sorted(set([BASELINE]) | set(display_keys))
    cells = []
    for key in sheet_keys:
        folder = FOLDER.get(key)
        if not folder:
            continue
        try:
            fpath = load_latest_n_tiff(folder)[0]   # 第一帧(k=1) TIFF
            raw = fr.imread_any(fpath)
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
    hd.text((6, 10), "左=原始扫描方向   右=算法校正后方向(op)   参考%s=原向" % BASELINE,
            fill=(20, 20, 20), font=hf)
    for i, (key, op, raw, oriented) in enumerate(cells):
        y = header_h + i * (TILE_H + GAP)
        sheet.paste(tile(raw, f"{key} 原始"), (0, y))
        sheet.paste(tile(oriented, f"{key} {V.OP_DISPLAY.get(op, op)}"), (TILE_W + GAP, y))
    sheet.save(out, 'JPEG', quality=Q)
    print(f"[联系表] 已生成: {out}  ({sheet.size[0]}x{sheet.size[1]}, {n} 组)")


# ---------- 报告生成 ----------
def build_report(avg, per_cell, display_keys, mode_label, footnote):
    display_keys = sorted(display_keys)          # 按序号顺序
    ranked = sorted(display_keys, key=lambda k: -avg[k]['total'])
    grp_label = "/".join(display_keys)
    V.make_vchart(avg, display_keys, CHART)
    make_firstframe_sheet(per_cell, display_keys)

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    L = []
    L.append('# V700test 平均成绩排名 · TIFF 专用测试（48bit 彩色 TIFF / 16bit 每通道 · 仅 %s · 基线 %s=100）' % (grp_label, BASELINE))
    L.append('')
    L.append(f'_生成: {now} | **{mode_label}** | 每组=6帧TIFF, 第k格跨组同帧 | 基线: {BASELINE}（48bit 彩色 TIFF 重扫）= 100 | 共 {len(display_keys)} 组_')
    L.append('')
    L.append('![V 型焦点模型图: 总分 vs 文件夹(TIFF)](rank_tiff_vchart.png)')
    L.append('')
    L.append('**本榜性质（重要）**')
    L.append('- 仅读取 `.tif/.tiff`，**忽略同文件夹内的旧 .jpg**（旧 JPEG 为 8bit+USM高+AE开，与本批 16bit TIFF 不可混排）。')
    L.append(f'- 基线为 **{BASELINE}（TIFF）**，非历史主榜的 A（8bit JPEG）。因此本榜分数与历史 A=100 主榜**不直接可比**，只用于参与各组 16bit TIFF 之间的横向比较。')
    L.append('- 各配置均为 48位彩色→TIFF / sRGB / 嵌入ICC / USM=无 / AE=关 重扫，设置一致，相对排序可信。')
    L.append('- 位深口径：读取时以 `IMREAD_UNCHANGED` 保留 **16bit/通道（uint16）**；锐度/跨度在 float32 上计算，色域按 16 级/轴量化时降到 8bit。故文档里的「16bit」指每通道位深，不是 16bit 灰度图。')
    L.append('')
    L.append('**指标说明**')
    L.append('- **锐度（edge_sharp）**：中心裁窗内「梯度 ÷ 局部对比度」中位数——与画面内容、整体曝光无关。权重 %.2f。' % W_EDGE)
    L.append('- **色域（gamut体积）**：Lab 色彩直方图占据格子数，反映色彩信息量与细节丰富度。权重 %.2f（注意其亦随锐度/噪点/内容正变，属相对区分信号而非绝对色域物理值）。' % W_GAMUT)
    L.append('- **跨度（span）**：像素明暗分布跨度（动态范围代理）。权重 %.2f。' % W_SPAN)
    L.append(f'- 总分 = {W_EDGE:.2f}×锐度 + {W_GAMUT:.2f}×色域 + {W_SPAN:.2f}×跨度，均以 {BASELINE} 为基准归一（{BASELINE}=100）。')
    L.append('- **扫描一致性** = 0.65×锐度std + 0.35×跨度std（不含色域std）。值越小=6 帧物理扫描越一致；仅作诊断。')
    L.append('')
    L.append('## 总分排名（6 格平均, %s=100 基准）' % BASELINE)
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
    notes = [f'- **{k}**：{v}' for k, v in CONFIG_NOTE.items() if k in display_keys]
    if notes:
        L.append('## 配置性质说明')
        L.append('')
        L.extend(notes)
        L.append('')
        L.append('> 斜楔组（W）的 6 格平均应理解为 2.0→2.25mm 段的平均表现，其总分与同段均匀垫高组（如 P=2.25mm）相近属正常，**不构成复现性验证**——真正的复现需另一组同高度均匀垫高扫描。')
        L.append('')
    L.append('## 每格明细（total_norm, %s 每格=100）' % BASELINE)
    L.append('')
    L.append('| 格 | %s | 其余各组 total | 同帧校验(内点<30 或 Pearson<0.85) |' % BASELINE)
    L.append('|---|---|---|---|')
    for k in range(1, N + 1):
        c = per_cell[str(k)]
        others = ", ".join(f"{key}={c[key]['total_norm']:.2f}" for key in display_keys if key != BASELINE)
        warns = [f"{key}⚠" for key in display_keys if key != BASELINE
                 and c[key]['inliers'] != '-' and (c[key]['inliers'] < 30 or c[key]['pearson'] < 0.85)]
        L.append(f"| {k} | {c[BASELINE]['total_norm']:.2f} | {others} | {', '.join(warns) or 'OK'} |")
    L.append('')
    L.append('> 方法: 每格以 %s_k 为参考, 各候选组经 8 向枚举 + 单应对齐到 %s 方向后, 在原图裁窗(无 warp)上算 edge/gamut/span; %s 恒为基线=100, 6 格取平均。' % (BASELINE, BASELINE, BASELINE) + footnote)
    L.append('> 同帧校验: SIFT 内点应 >30, 裁窗 Pearson 应 >0.85; 出现 ⚠ 仅表示该格对齐质量偏低, 指标仍在正确裁出的原图像素上算, 不代表错误。横/竖构图差异由 8 向枚举自动吸收。')
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
    L.append(f'异常判据 = **物理分 = {W_PHYS_EDGE:.2f}×锐度 + {W_PHYS_SPAN:.2f}×跨度**（总分权重里锐度:跨度 = {W_EDGE:.2f}:{W_SPAN:.2f} 内部归一，不含色域），'
             f'对每组 6 格做统计，仅标记低于该组均值 **−8% 以上**（{BASELINE} 基准=100）的单张。')
    L.append('色域由每帧底片内容主导、非扫描物理量，故不参与异常触发。锐度口径为「梯度 ÷ 局部对比度」中位数，对画面内容与整体曝光不敏感，故偏离更值得怀疑是真实光学问题。')
    L.append('**归因判别**：若某一帧在**多数配置**中一致偏低 → 属该底片格自身内容/拍摄端偏软；若仅**个别配置**某帧偏低 → 指向该次扫描对焦波动/牛顿环/摩尔纹/灰尘。需用户确认后才做定向检测。')
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
                L.append(f"| {key} | {k} | {v:.2f} | {m:.2f} | {dev:+.1f}% | 建议确认是否做异常检测 |")
    if not any_flag:
        L.append('| — | — | — | — | — | 本批无单张显著低分 |')
    L.append('')
    L.append('> 异常单张已在表中标出，可回到原图对应格放大复核。')
    L.append('')

    with open(MD, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    with open(OLD_JSON, 'w', encoding='utf-8') as f:
        json.dump({'average': avg, 'ranking': ranked, 'per_cell': per_cell,
                   'baseline': BASELINE, 'only_tiff': True},
                  f, ensure_ascii=False, indent=2)

    print(f"\n排名: " + " > ".join(f"{k}={avg[k]['total']:.2f}" for k in ranked))
    print(f"报告: {MD}\nJSON: {OLD_JSON}\n图表: {CHART}")


# ---------- 增量加测 ----------
def _to_native(x):
    """numpy 标量/容器 → Python 原生, 保证 json.dump 稳定。"""
    if isinstance(x, dict):
        return {k: _to_native(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_to_native(v) for v in x]
    if isinstance(x, np.floating):
        return float(x)
    if isinstance(x, np.integer):
        return int(x)
    return x


def add_group(key, folder):
    """增量加测单个组: 只算新组(6帧 + 基线 M), 合并进既有结果, 不重算其它组。"""
    key = key.upper()
    FOLDER[key] = folder

    if not os.path.exists(OLD_JSON):
        raise SystemExit(f"找不到既有结果 {OLD_JSON}，请先跑一次全量: python rank_tiff.py")
    with open(OLD_JSON, encoding='utf-8') as f:
        data = json.load(f)
    avg = data['average']
    per_cell = data['per_cell']

    if key in per_cell.get('1', {}):
        print(f'[提示] {key} 已存在于既有结果，将覆盖重算')

    paths = load_latest_n_tiff(folder)
    print(f"[{key}] 取 {len(paths)} 张TIFF: " + ", ".join(os.path.basename(p) for p in paths))
    if len(paths) < N:
        raise SystemExit(f"{key} 仅 {len(paths)} 帧 < {N}")
    m_paths = load_latest_n_tiff(FOLDER[BASELINE])
    if len(m_paths) < N:
        raise SystemExit(f"基线 {BASELINE} 仅 {len(m_paths)} 帧 < {N}, 无法加测")
    group_frames = {key: paths, BASELINE: m_paths}
    active = [BASELINE, key]

    consensus = orientation_pre_pass(group_frames, active)
    for k in range(1, N + 1):
        _, cell, detail = _compute_cell((k, group_frames, active, consensus))
        for line in detail:
            print(line)
        per_cell[str(k)][key] = _to_native(cell[key])

    def m_of(stat):
        return float(np.mean([per_cell[str(k)][key][stat] for k in range(1, N + 1)]))

    def s_of(stat):
        return float(np.std([per_cell[str(k)][key][stat] for k in range(1, N + 1)]))

    avg[key] = dict(
        edge=m_of('edge_norm'), gamut=m_of('gamut_norm'), span=m_of('span_norm'),
        total=m_of('total_norm'),
        edge_std=s_of('edge_norm'), gamut_std=s_of('gamut_norm'),
        span_std=s_of('span_norm'), total_std=s_of('total_norm'),
    )

    display_keys = sorted(per_cell['1'].keys())
    mode = f"TIFF 专用测试（基线 {BASELINE}，{key} 为单独加测后合并入既有结果）"
    footnote = f"{key} 单独加测(仅读 {key} 6帧+基线 {BASELINE})后合并入既有 per_cell；其余组沿用既有结果。"
    build_report(avg, per_cell, display_keys, mode, footnote)
    save_registry()
    print(f'[完成] {key} 已合并, 参与组:', display_keys)


def list_groups():
    print("已登记的 TIFF 组（组代号 → 文件夹）:")
    builtin = dict(DEFAULT_GROUPS)
    for k in sorted(FOLDER):
        tag = '' if k in builtin else '  [加测登记]'
        print(f"  {k:4s} → {FOLDER[k]}{tag}")
    print(f"基线: {BASELINE}")


# ---------- 入口 ----------
def main():
    ap = argparse.ArgumentParser(description='V700test 底片扫描排名 · TIFF 专用测试')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--groups', nargs='+', metavar='KEY', default=None,
                      help='全量重算: 指定参与组(默认全部), 例: --groups M O Q')
    mode.add_argument('--add', nargs='+', metavar=('KEY', 'FOLDER'), default=None,
                      help='增量加测: --add ZI ZI_AR玻璃改_超薄片夹_3.5_M10')
    mode.add_argument('--list', action='store_true',
                      help='列出已登记的组代号与文件夹')
    ap.add_argument('--key', default=None,
                    help='加测旧写法: --key ZI --folder ZI_AR玻璃改_... （与 --add KEY FOLDER 等价）')
    ap.add_argument('--folder', default=None,
                    help='加测时指定文件夹名，配合 --add KEY 或 --key KEY')
    args = ap.parse_args()

    extra = load_registry()
    if extra:
        print(f"[登记表] 载入加测组: {extra}")

    if args.list:
        list_groups()
        return

    # 加测: 兼容三种等价写法
    #   1) --add ZI ZI_文件夹名
    #   2) --add ZI --folder ZI_文件夹名
    #   3) --key ZI --folder ZI_文件夹名 (旧 add_group_merge.py 写法)
    add_key = add_folder = None
    if args.add:
        add_key = args.add[0]
        add_folder = args.add[1] if len(args.add) > 1 else args.folder
    elif args.key:
        add_key = args.key
        add_folder = args.folder
    if add_key:
        if not add_folder:
            raise SystemExit('加测需文件夹名: --add KEY FOLDER / --add KEY --folder FOLDER / --key KEY --folder FOLDER')
        add_group(add_key, add_folder)
        return
    if args.folder:
        raise SystemExit('--folder 只在加测模式(--add/--key)下使用')

    if args.groups:
        full = dict(FOLDER)          # DEFAULT_GROUPS + groups.json 里的加测组
        selected = {}
        for u in [g.upper() for g in args.groups]:
            if u in full:
                selected[u] = full[u]
            else:
                print(f"[忽略] 未知组 {u}（--list 可查看全部已登记组）")
        if BASELINE not in selected:
            raise SystemExit(f"必须包含基线 {BASELINE}")
        FOLDER.clear()
        FOLDER.update(selected)

    active = list(FOLDER.keys())
    print(f"[TIFF 专用] 参与组: {active}  基线: {BASELINE}")
    per_cell, active = compute_active(active)
    avg = aggregate(per_cell, active)
    mode = f"TIFF 专用测试（基线 {BASELINE}, 仅 {'/'.join(active)} 16bit TIFF）"
    footnote = "本批仅 TIFF, 未复用旧主榜 JSON; 与 A=100 主榜不直接可比。"
    build_report(avg, per_cell, active, mode, footnote)


if __name__ == '__main__':
    main()
