# -*- coding: utf-8 -*-
"""流程方案统一入口：python run_flow.py --flow rank [--drive D] [--launch]

从 flows.json 读方案（rank / jpg_social / tiff_post），组装 RunConfig，一次启动跑完整流程。
预览页选项与全选/单选不进方案：沿用软件已保存状态、固定全选。

铁律（沿用 epson_one_click.py）：
  * 默认 attach 模式，不启停软件；扫描仪冷启动那一步仍由用户手动完成。
  * --launch 时才自己启动，且退出时走三步优雅关闭（点取消→点关闭→等进程退出）。
  * 绝不 taskkill /F、绝不 WM_CLOSE 强关子窗口。

安装初始化：`python run_flow.py --init` —— 只在装完 skill 时问一次存储磁盘（展示各盘
剩余空间 + 4800dpi 可存 JPG/TIFF 张数），结果写入 config.json；之后流程直接读，不再问。

命令行覆盖：--drive 临时改盘 / --launch 自己启动 / --dry-run 不扫描 / --select-one 只选1张 /
            --foreground 前台鼠标模式 / --batch 张数 / --interval 间隔 / --list 列出方案。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import epson_one_click as e

BASE = Path(__file__).resolve().parent
FLOWS_PATH = BASE / "flows.json"


def load_flows() -> dict:
    if not FLOWS_PATH.exists():
        raise SystemExit(f"方案库不存在: {FLOWS_PATH}")
    return json.loads(FLOWS_PATH.read_text(encoding="utf-8"))


def build_config(data: dict, name: str, args) -> e.RunConfig:
    flows = data.get("方案", {})
    if name not in flows:
        raise SystemExit(f"未知方案 {name!r}，可选: {', '.join(flows)}")
    f = flows[name]
    main = f.get("main", {})
    cd = f.get("config_dialog", {})
    fs = f.get("filesettings", {})
    bh = f.get("behavior", {})
    topt = fs.get("TIFF子选项")

    cfg = e.RunConfig(
        profile="rank" if "TIFF" in str(fs.get("图像格式", "")).upper() else "share",
        flow_name=name,
        scanner=f.get("扫描仪", "") or "",
        # 磁盘来自安装初始化时存下的配置（config.json 的「存储磁盘」）；
        # --drive 可临时覆盖；老方案里显式填的「磁盘」也仍兼容。
        drive=args.drive or e.load_storage_drive() or (f.get("磁盘", "") or ""),
        # main 组
        document_type=main.get("文档类型", "胶片(带胶片支架)"),
        film_type=main.get("胶片类型", "正片"),
        image_type=main.get("图像类型", "48-位全彩"),
        resolution=main.get("分辨率", "4800"),
        usm=main.get("USM锐化", False),
        usm_level=main.get("USM级别", "高"),
        ice=main.get("DIGITAL_ICE", False),
        ice_mode=main.get("DIGITAL_ICE模式", ""),
        # config_dialog 组（config_pages 优先，含预览页+彩色页；兼容旧 config_dialog_items）
        config_dialog_items=cd if cd else None,
        config_pages=f.get("config_pages"),
        color_mode=("色彩控制" if cd.get("色彩控制") else
                    "ICM" if cd.get("ICM") else "无色彩校正"),
        # filesettings 组
        image_format=fs.get("图像格式", "TIFF(*.tif)"),
        jpeg_quality=fs.get("JPEG质量"),
        filename_prefix=fs.get("文件名前缀", "img"),
        start_number=fs.get("起始号码", "001"),
        overwrite=fs.get("覆盖同名", False),
        open_folder=fs.get("扫描后打开文件夹", True),
        folder_name=args.folder or (fs.get("文件夹名", "") or ""),
        tiff_options=topt,
        # behavior 组
        skip_config=bh.get("跳过配置对话框", False),
        use_saved_filesettings=bh.get("跳过文件保存设置", False),
        wait_seconds=bh.get("等待秒数", 600),
        batch=args.batch if args.batch is not None else bh.get("连续张数", 1),
        prompt=bh.get("逐张确认", True),
        interval=args.interval if args.interval is not None else bh.get("批内间隔秒", 3),
        select_one=args.select_one,
        select_index=args.select_index,
        dry_run=args.dry_run,
        launch=args.launch,
    )
    # 前台/后台模式（全局）——方案值可被 --foreground 覆盖
    e.BACKGROUND = not (args.foreground or bh.get("前台鼠标模式", False))
    return cfg


def main():
    ap = argparse.ArgumentParser(description="EPSON Scan 流程方案入口（flows.json）")
    ap.add_argument("--flow", default=None, help="方案名: rank / jpg_social / tiff_post")
    ap.add_argument("--init", action="store_true", help="安装初始化：问一次存储磁盘并存入 config.json（仅此一次）")
    ap.add_argument("--list", action="store_true", help="列出所有方案及描述")
    ap.add_argument("--drive", default="", help="覆盖方案里的磁盘盘符（如 D）")
    ap.add_argument("--folder", default="", help="覆盖方案里新建的文件夹名")
    ap.add_argument("--launch", action="store_true", help="自己启动软件（默认 attach 已打开的）")
    ap.add_argument("--dry-run", action="store_true", help="不真正点扫描")
    ap.add_argument("--select-one", action="store_true", help="只选1张（测试用）")
    ap.add_argument("--select-index", type=int, default=1, help="--select-one 时选第几帧（从1开始，默认1；测第2帧传2）")
    ap.add_argument("--foreground", action="store_true", help="前台鼠标模式（默认后台消息）")
    ap.add_argument("--batch", type=int, default=None, help="覆盖连续张数")
    ap.add_argument("--interval", type=int, default=None, help="覆盖批内间隔秒")
    ap.add_argument("--full", action="store_true", help="强制完整流程（重设所有配置并刷新快照）；默认有匹配快照即走快速模式")
    ap.add_argument("--fast", action="store_true", help="强制快速模式（预览→全选→扫描，跳过配置）")
    args = ap.parse_args()

    # 【安装初始化】只在装完 skill / 想换盘时运行一次：问存储磁盘 → 写 config.json。
    # 之后所有 scan 流程直接读配置，不再每次询问。
    if args.init:
        e.init_storage()
        return

    data = load_flows()
    flows = data.get("方案", {})

    if args.list or not args.flow:
        default = data.get("默认方案", "")
        print(f"方案库: {FLOWS_PATH}  默认: {default}\n")
        for name, f in flows.items():
            mark = " [默认]" if name == default else ""
            print(f"  {name:<12}{mark}  {f.get('描述', '')}")
        print("\n用法: python run_flow.py --init   # 安装后先运行一次，选存储磁盘")
        print("      python run_flow.py --flow <名称> [--drive D] [--launch] [--dry-run]")
        if not args.flow and not args.list:
            print("\n未指定 --flow，仅列出方案（未启动任何操作）。")
        return

    cfg = build_config(data, args.flow, args)
    drive = (cfg.drive or "").strip().upper().rstrip(":")
    if not drive:
        raise SystemExit("未设置存储磁盘：先运行一次 `python run_flow.py --init` 选盘，"
                         "或本次临时指定 `--drive D`")
    cfg.drive = drive
    cfg.output_dir = f"{drive}:\\{cfg.out_folder().replace('/', chr(92))}"

    e.log(f"[方案 {args.flow}] {f.get('描述', '')}" if (f := flows[args.flow]) else "")
    e.log(f"目标磁盘={drive}:  输出目录={cfg.output_dir}  后台消息模式={e.BACKGROUND}")

    # 【效率优化】流程指纹 + 快速/完整模式决策
    fingerprint = e.flow_fingerprint(flows[args.flow], drive, cfg.out_folder())
    cfg._fingerprint = fingerprint
    if args.full:
        use_fast = False
        e.log("模式: 完整（--full 强制）")
    elif args.fast:
        use_fast = True
        e.log("模式: 快速（--fast 强制）")
    elif e.can_fast(args.flow, fingerprint):
        use_fast = True
        e.log("模式: 快速（流程需求未变，沿用上次完整配置）")
    else:
        use_fast = False
        e.log("模式: 完整（无匹配快照或流程需求已变）")

    # 只有自己启动的软件才由脚本负责优雅关闭（三步铁律，见 epson_one_click）
    if cfg.launch:
        e._install_exit_hook(should_close=True)
    try:
        if use_fast:
            e.run_fast(cfg)
        else:
            e.run(cfg)
    finally:
        if cfg.launch:
            try:
                e.close_epson_gracefully()
            except Exception as ex:
                e.log(f"WARN 退出钩子关闭 EPSON 失败: {ex}")


if __name__ == "__main__":
    main()
