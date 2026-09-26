#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 focus_history.json / color_history.json 自动生成 打分排名.md
==============================================================
每次跑完 focus_compare.py / color_compare.py 后, 运行本脚本刷新总榜:

  python gen_rank_md.py

排名规则:
  a/ 标靶: 按 sharp(边缘锐度) 降序, 越高越清晰
  b/ 底片: 按 DR(动态范围, 档) 降序, 越高越好
文件夹名从各 scan 的 dir 字段父目录提取。
"""
import json
import os
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
FOCUS = os.path.join(BASE, "focus_history.json")
COLOR = os.path.join(BASE, "color_history.json")
OUT = os.path.join(BASE, "打分排名.md")


def folder_of(dirpath):
    """从 ...\\<folder>\\a 或 ...\\<folder>\\b 提取 <folder>"""
    return os.path.basename(os.path.dirname(dirpath))


def mediant(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return 0.0
    if n % 2:
        return xs[n // 2]
    return (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("scans", [])


def gen():
    focus = load(FOCUS)
    color = load(COLOR)

    # ---- a/ 标靶 ----
    a_rows = []
    for s in focus:
        folder = folder_of(s["dir"])
        a_rows.append({
            "folder": folder,
            "sharp": s.get("sharp", s.get("score", 0.0)),
            "res_limit_px": s.get("res_limit_px"),
            "lim_v": s.get("lim_v"),
            "lim_h": s.get("lim_h"),
            "g5v": s.get("g5v"),
            "g5h": s.get("g5h"),
            "ca": s.get("ca"),
            "ca_p90": s.get("ca_p90"),
            "n": s.get("n_images", 0),
            "ts": s.get("ts", ""),
        })
    a_rows.sort(key=lambda r: r["sharp"], reverse=True)

    # ---- b/ 底片 ----
    b_rows = []
    for s in color:
        folder = folder_of(s["dir"])
        b_rows.append({
            "folder": folder,
            "dr": s.get("dr", s.get("score", 0.0)),
            "cast_R": s.get("cast_R"),
            "cast_B": s.get("cast_B"),
            "cast_mag": s.get("cast_mag"),
            "clip_lo": s.get("clip_lo"),
            "clip_hi": s.get("clip_hi"),
            "n": s.get("n_images", 0),
            "ts": s.get("ts", ""),
        })
    b_rows.sort(key=lambda r: r["dr"], reverse=True)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = []
    L.append("# 扫描对焦打分总榜\n")
    L.append(f"> 自动生成于 {now} · 数据源 `focus_history.json` / `color_history.json`\n")
    L.append("> 每次打分后运行 `python gen_rank_md.py` 刷新本榜。\n")

    # a 表
    L.append("\n## a/ 标靶排名（sharp = 块内细密黑白精度锐度，越高越清晰）\n")
    L.append("> 分方向限量分辨率：**三竖** / **三横** 列 = 该方向极限可分辨的 USAF 组号（越大=能分辨越细=越清晰）；g5三竖/g5三横 = 在 group 5-2 的归一化调制（group1=1.0，越大越清晰）。三横普遍比三竖差约 1 组，为扫描车运动模糊所致。\n")
    L.append("| 排名 | 文件夹 | sharp(细密精度) | 极限周期(px) | **三竖限量** | **三横限量** | g5三竖 | g5三横 | 色散中位(px) | 色散 p90(px) | 帧数 | 打分时间 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(a_rows, 1):
        ca = f"{r['ca']:.3f}" if r["ca"] is not None else "—"
        ca90 = f"{r['ca_p90']:.3f}" if r["ca_p90"] is not None else "—"
        rl = f"{r['res_limit_px']:.1f}" if r.get("res_limit_px") is not None else "—"
        lv = f"{r['lim_v']:.1f}" if r.get("lim_v") is not None else "—"
        lh = f"{r['lim_h']:.1f}" if r.get("lim_h") is not None else "—"
        g5v = f"{r['g5v']:.2f}" if r.get("g5v") is not None else "—"
        g5h = f"{r['g5h']:.2f}" if r.get("g5h") is not None else "—"
        ts = r["ts"][:16].replace("T", " ") if r["ts"] else ""
        L.append(f"| {i} | `{r['folder']}` | {r['sharp']:.4f} | {rl} | {lv} | {lh} | {g5v} | {g5h} | {ca} | {ca90} | {r['n']} | {ts} |")

    # b 表
    L.append("\n## b/ 彩色底片排名（动态范围 DR，档数越高越好）\n")
    L.append("| 排名 | 文件夹 | DR(档) | R/G | B/G | 偏色量 | 裁切↓/↑% | 帧数 | 打分时间 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(b_rows, 1):
        rg = f"{r['cast_R']:.3f}" if r["cast_R"] is not None else "—"
        bg = f"{r['cast_B']:.3f}" if r["cast_B"] is not None else "—"
        mag = f"{r['cast_mag']:.3f}" if r["cast_mag"] is not None else "—"
        lo = r["clip_lo"] if r["clip_lo"] is not None else 0
        hi = r["clip_hi"] if r["clip_hi"] is not None else 0
        clip = f"{lo:.2f}% / {hi:.2f}%"  # 历史里 clip 已是百分比数值(0.11=0.11%)
        ts = r["ts"][:16].replace("T", " ") if r["ts"] else ""
        L.append(f"| {i} | `{r['folder']}` | {r['dr']:.2f} | {rg} | {bg} | {mag} | {clip} | {r['n']} | {ts} |")

    # 小结
    L.append("\n## 小结\n")
    if a_rows:
        L.append(f"- a/ 标靶共 {len(a_rows)} 个目录，当前最清晰：`{a_rows[0]['folder']}`（sharp={a_rows[0]['sharp']:.4f}，三竖限量≈group {a_rows[0].get('lim_v')}，三横限量≈group {a_rows[0].get('lim_h')}）")
    else:
        L.append("- a/ 标靶：暂无数据")
    if b_rows:
        L.append(f"- b/ 底片共 {len(b_rows)} 个目录，当前动态范围最高：`{b_rows[0]['folder']}`（DR={b_rows[0]['dr']:.2f} 档）")
    else:
        L.append("- b/ 底片：本次未测（仅跑 a/），无数据")
    L.append("\n> 说明：a/ 的 sharp 自 2026-09-01 起改为「块内细密黑白精度锐度」（看极限可分辨的三横三竖组），与之前(边缘梯度法)不具可比性，旧条目已清空重打；极限周期(px) 越小=能分辨越细=越清晰。b/ 的偏色量受单帧画面内容影响大，横向比通道平衡需取内容相近的帧。")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"已生成 {OUT}  (a={len(a_rows)} 目录, b={len(b_rows)} 目录)")


if __name__ == "__main__":
    gen()
