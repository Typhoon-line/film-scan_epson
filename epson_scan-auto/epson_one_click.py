# -*- coding: utf-8 -*-
"""EPSON Scan 3.9 一键扫描（Windows GUI 自动化，pywinauto / win32 backend）。

【重要 · 本机硬件约束】扫描仪必须"物理断电重启"后第一次打开 EPSON Scan 才正常；
一旦正常，可连续扫一批。因此本脚本默认 **attach 模式**：只连接你已打开的正常窗口，
绝不自己启动/关闭软件、绝不反复启停进程。冷启动那一步只能你手动做。

用途：把截图《基础操作流程》的 12 步手工操作打包成一次点击（--batch N 可连续 N 张）：
  专业模式 -> 胶片/正片 -> 48bit/分辨率 -> USM/DICE
  -> 文件保存设置(路径/前缀/格式) -> 预览 -> 只选 1 张 -> 扫描 -> 等待新文件落盘

设计要点（来自实测控件树）：
  * 经典 EPSON Scan 3.9（escndv.exe），无官方 CLI；走 Win32 控件自动化。
  * 主窗口标题 "EPSON Scan"、class #32770；控件用「名称」定位（名称=当前显示值），
    每次改完名称会变，所以每一步都重新按角色查找，不缓存旧名称。
  * 支持两台扫描仪：EPSON GT-X970 / EPSON Perfection V700/V750。
  * 提供两套档位：share（JPEG+USM高，对应截图）与 rank（48bit TIFF+USM无，
    对应排名 SOP）。避免一键把排名数据污染。
  * --dry-run 只走到"只选 1 张"，不真正点扫描；--probe 只 dump 控件树。

标准操作流程：
  ① 手动：扫描仪断电重启 -> 打开 EPSON Scan -> 选扫描仪 -> 进入专业模式
  ② python epson_one_click.py --profile rank --batch 5     # 脚本接管设置+扫描
  ③ 每张之间脚本提示换片，按 Enter 继续；输入 q 提前退出

用法：
  python epson_one_click.py --profile rank --dry-run        # 只走设置，不扫描
  python epson_one_click.py --profile rank                  # 扫 1 张
  python epson_one_click.py --profile rank --batch 5        # 连续扫 5 张
  python epson_one_click.py --profile share --scanner "EPSON GT-X970"
  python epson_one_click.py --probe                         # 连接已打开窗口 dump 控件树
  python epson_one_click.py --launch --probe                # 仅探测时才自己启动（需先冷启动）

依赖：pip install pywinauto
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from pywinauto import Application, Desktop

try:
    from pywinauto.controls.win32_controls import ComboBoxWrapper
except Exception:
    ComboBoxWrapper = None

EXE = r"C:\Windows\twain_32\escndv\escndv.exe"
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
LOG_PATH = BASE_DIR / "run.log"
# 【2026-11-27 效率优化】流程状态快照：记录每个方案最近一次完整配置+时间。
# 下次同一方案（流程需求未变）可直接走快速模式：预览→全选→扫描，跳过所有配置步骤。
STATE_PATH = BASE_DIR / "scan_state.json"

# 【2026-11-26 用户要求】每个 GUI 操作之间统一间隔 0.5s，避免连续点太快失效。
# 所有改变状态的动作（点击/下拉选择/输入）完成后都调用 op_pause()。
OP_DELAY = 0.5

# 【2026-11-27 用户要求】选择模式后至少等 5 秒稳定，再判定窗口重建/回读。
# 切模式会重建整个窗口（控件数 16/36/51/110 突变），太快读会读到中间态。
MODE_SETTLE_SEC = 5.0

# 【2026-11-27 用户要求】默认后台模式：绝不移动鼠标 / 不抢前台焦点。
# 实测已验证 BM_CLICK(PostMessage) 能驱动 EPSON 主窗口自绘图标按钮和对话框按钮，
# 全程前台窗口与鼠标位置不变（见 probe_msg_click.py）。
# 需要真实鼠标时（例如某些自绘控件不响应消息）才用 --foreground 切回。
BACKGROUND = True
BM_CLICK = 0x00F5
SCAN_FOLDER_NAME = "A_爱普生扫描"   # 目标磁盘根目录下固定新建的文件夹名


def op_pause():
    time.sleep(OP_DELAY)


def msg_click_handle(hwnd) -> bool:
    """后台点击：异步投递 BM_CLICK 给控件句柄。
    不移动鼠标、不改变前台窗口、不需要活动桌面 —— 用户可继续干别的事。
    """
    try:
        import win32gui
        win32gui.PostMessage(int(hwnd), BM_CLICK, 0, 0)
        op_pause()
        return True
    except Exception as ex:
        log(f"WARN BM_CLICK 消息点击失败 handle={hwnd}: {ex}")
        return False

# ---------------------------------------------------------------- 配置

PROFILES = {
    # 截图档：JPEG 高质量 + USM 高（分享/看图用）
    "share": {
        "mode": "专业模式",
        "document_type": "胶片(带胶片支架)",
        "film_type": "正片",
        "image_type": "48-位全彩",
        "resolution": "4800",
        "usm": True,
        "usm_level": "高",
        "ice": False,
        "image_format": "全真数码影像技术 II (JPEG)(*.jpg)",
        "jpeg_quality": 1,
        "output_dir": str(BASE_DIR / "out_share"),
        "filename_prefix": "img",
        "start_number": "001",
        "overwrite": False,
    },
    # 排名档：48bit TIFF + USM 关 + 自动曝光关（与历史榜单同口径）
    "rank": {
        "mode": "专业模式",
        "document_type": "胶片(带胶片支架)",
        "film_type": "正片",
        "image_type": "48-位全彩",
        "resolution": "4800",
        "usm": False,
        "usm_level": "高",
        "ice": False,
        "image_format": "TIFF(*.tif)",
        "jpeg_quality": None,
        "output_dir": str(BASE_DIR / "out_rank"),
        "filename_prefix": "img",
        "start_number": "001",
        "overwrite": False,
    },
}


@dataclass
class RunConfig:
    profile: str = "rank"
    scanner: str = ""  # 默认不指定，机型选择对话框直接点确定用首项（GT-X970）
    mode: str = "专业模式"
    document_type: str = "胶片(带胶片支架)"
    film_type: str = "正片"
    image_type: str = "48-位全彩"
    resolution: str = "4800"
    usm: bool = False
    usm_level: str = "高"
    ice: bool = False
    image_format: str = "TIFF(*.tif)"
    jpeg_quality: int | None = None
    output_dir: str = ""
    filename_prefix: str = "img"
    start_number: str = "001"
    overwrite: bool = False
    preset: str = ""            # 若给出，优先走「当前设置」预设，跳过逐项设置
    use_saved_filesettings: bool = False  # 若为 True，跳过文件保存设置步骤
    wait_seconds: int = 600     # 等待新文件落盘超时
    dry_run: bool = False
    probe: bool = False
    launch: bool = False        # 仅探测时才自己启动软件；默认 attach 已打开的窗口
    batch: int = 1              # 连续扫描张数
    color_mode: str = "无色彩校正"  # 配置对话框色彩模式：无色彩校正/色彩控制/ICM
    skip_config: bool = True    # 默认跳过配置对话框（rank 默认已是"无色彩校正+曝光关"；该对话框有"选项"子窗口需多次确定/取消）
    prompt: bool = True         # 每张之间等待用户按 Enter 换片
    interval: int = 3           # --no-prompt 时每张之间的等待秒数
    test_ui: bool = False       # 本次专项测试：配置对话框/TIFF子选项/打开文件夹勾选
    drive: str = ""             # 执行前问用户：扫描文件存到哪个磁盘（如 D）
    select_one: bool = False    # 正常流程=全选扫描（默认）；仅测试时 --select-one 只选第1张
    select_index: int = 1       # --select-one 时选第几帧（从1开始，默认1；测试可指定第2帧）
    # 【2026-11-27 流程方案】由 flows.json 注入；None 时保持旧行为（向后兼容）
    config_dialog_items: dict | None = None  # 配置对话框彩色页目标项 {名称: 值}
    config_pages: dict | None = None         # 配置对话框按页目标 {页名: {控件名: 值}}，含预览页滑块
    ice_mode: str = ""                       # DIGITAL ICE 模式下拉目标（如「速度」/「质量」）；空=不改
    tiff_options: dict | None = None         # TIFF 子选项 {位元组顺序,嵌入ICC,彩色灰度压缩,黑白压缩}
    open_folder: bool | None = None          # 扫描后打开图像文件夹目标态
    flow_name: str = ""                      # 当前方案名（仅日志用）
    folder_name: str = ""                    # 目标磁盘根目录下新建的文件夹名；空=用 SCAN_FOLDER_NAME

    def out_folder(self) -> str:
        """本方案实际使用的文件夹名（方案未指定时回退全局默认）。"""
        return (self.folder_name or SCAN_FOLDER_NAME).strip()


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config(args) -> RunConfig:
    d = dict(PROFILES[args.profile])
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            prof = user.get("profiles", {}).get(args.profile, {})
            d.update(prof)
            if "scanner" in user:
                d["scanner"] = user["scanner"]
        except Exception as e:
            log(f"WARN config.json 读取失败: {e}")
    d.update({k: v for k, v in vars(args).items()
              if v is not None and k in RunConfig.__dataclass_fields__ and k != "profile"})
    return RunConfig(**d)


# ---------------------------------------------------------------- 控件工具

def top_windows():
    """返回 spec（WindowSpecification），不是 wrapper。

    关键：Desktop.windows() 返回的是 wrapper，没有 .child_window()/.descendants()
    的查找语义（wrapper 上 .child_window 根本不存在，调用会抛 AttributeError 并
    被各处 except 吞掉，导致控件定位全空）。必须转成 spec 再返回。

    Windows 窗口枚举竞态：进程/窗口在枚举瞬间消失时，Desktop.windows() 内部构造
    wrapper 会抛 InvalidWindowHandle——必须逐个捕获跳过，否则整个枚举崩掉。
    """
    d = Desktop(backend="win32")
    try:
        wrappers = d.windows()
    except Exception:
        return []
    out = []
    for w in wrappers:
        try:
            if w.is_visible():
                out.append(d.window(handle=w.handle))
        except Exception:
            continue  # 窗口刚销毁，跳过
    return out


def safe_windows():
    """安全枚举所有顶层窗口 wrapper，跳过枚举瞬间销毁的失效句柄。

    【2026-11-26 修复】此前误写成递归调用自身（ws = safe_windows()），导致
    RecursionError 被吞、永远返回空列表——launch 模式机型选择对话框因此"找不到"。
    """
    out = []
    try:
        ws = Desktop(backend="win32").windows()
    except Exception:
        return out
    for w in ws:
        try:
            if w.is_visible():
                out.append(w)
        except Exception:
            continue
    return out


def find_window(title: str, cls: str | None = None, contains: bool = False):
    for w in top_windows():
        try:
            t = w.window_text()
            ok = (title in t) if contains else (t == title)
            if ok and (cls is None or w.class_name() == cls):
                return w
        except Exception:
            pass
    return None


def wait_window(title, cls=None, contains=False, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        w = find_window(title, cls, contains)
        if w:
            return w
        time.sleep(0.3)
    return None


def ctrl_spec(win, role, *, name=None, cls=None, found_index=None):
    """在窗口内按名称/类名/序号找控件，返回 WindowSpecification。"""
    kw = {}
    if name is not None:
        kw["title"] = name
    if cls is not None:
        kw["class_name"] = cls
    if found_index is not None:
        kw["found_index"] = found_index
    return win.child_window(**kw)


def find_ctrl_any(win, names, cls=None):
    """按多个可能的名称查找，返回第一个存在的 spec。"""
    for n in names:
        try:
            spec = win.child_window(title=n, **({"class_name": cls} if cls else {}))
            if spec.exists(timeout=0.2):
                return spec
        except Exception:
            pass
    return None


def move_window_beside(win, anchor_win, gap=8):
    """把预览窗口移到主窗口右侧并排，方便对照操作。

    只有当目标位置完全落在屏幕工作区内才移动，否则尝试下方，再不行保持原位
    （避免小屏幕把窗口挪到屏幕外导致按钮点不到）。
    """
    try:
        import ctypes
        u = ctypes.windll.user32
        ar = anchor_win.rectangle()
        # 屏幕工作区 SPI_GETWORKAREA
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        mi = RECT()
        u.SystemParametersInfoW(0x0030, 0, ctypes.byref(mi), 0)
        pr = win.rectangle()
        pw, ph = pr.right - pr.left, pr.bottom - pr.top

        def fits(x, y):
            return (x >= mi.left and y >= mi.top and
                    x + pw <= mi.right and y + ph <= mi.bottom)

        # 优先右侧；放不下再试下方
        candidates = [(ar.right + gap, ar.top), (ar.left, ar.bottom + gap)]
        for new_x, new_y in candidates:
            if fits(new_x, new_y):
                win.move_window(new_x, new_y, width=pw, height=ph, repaint=True)
                log(f"预览窗口移到主窗口{'右侧' if new_y == ar.top else '下方'} ({new_x},{new_y})")
                return True
        log("WARN 屏幕空间不足，预览窗口保持原位")
        return False
    except Exception as e:
        log(f"WARN 移动预览窗口失败: {e}")
        return False


def tab_current(win) -> int | None:
    """读 SysTabControl32 当前页：0=普通，1=缩略图。用 TCM_GETCURSEL。"""
    try:
        import ctypes
        t = next((x for x in win.descendants(class_name="SysTabControl32")
                  if x.is_visible()), None)
        if t is None:
            return None
        return int(ctypes.windll.user32.SendMessageW(t.handle, 0x1307, 0, 0))
    except Exception:
        return None


def thumb_checked(win) -> dict[int, bool]:
    """读每张缩略图的勾选状态：{图号: 是否勾选}。仅统计文字为纯数字的可见按钮。

    注意：win32 backend 下 descendants() 返回的已经是 wrapper（ButtonWrapper），
    不能再调 .wrapper_object()（会抛 AttributeError）。wrapper 自带
    window_text()/is_visible()/is_checked()。
    """
    out = {}
    try:
        for b in win.descendants(class_name="Button"):
            try:
                t = b.window_text().strip()
                if not t.isdigit() or not b.is_visible():
                    continue
                has = hasattr(b, "is_checked")
                out[int(t)] = bool(b.is_checked()) if has else False
            except Exception:
                pass
    except Exception:
        pass
    return out


def find_visible_ctrl(win, names, cls="Button"):
    """按名称找【可见】控件。EPSON 预览窗口里「全选(&A)」有两个（一个是
    Tab 页切换残留的隐藏控件），必须点可见的那个，否则点击无效。"""
    for n in names:
        try:
            for spec in win.descendants(class_name=cls):
                try:
                    if spec.window_text() == n and spec.is_visible():
                        return spec
                except Exception:
                    pass
        except Exception:
            pass
    return None


def bring_to_front(win) -> bool:
    """用 Win32 API 强制把窗口提到前台（比 set_focus 更硬，能抢过遮挡窗口）。"""
    try:
        import ctypes
        h = win.handle if hasattr(win, "handle") else win.wrapper_object().handle
        u = ctypes.windll.user32
        u.ShowWindow(h, 9)          # SW_RESTORE
        u.BringWindowToTop(h)
        u.SetForegroundWindow(h)
        time.sleep(0.25)
        return True
    except Exception:
        return False


def close_blocking_explorers() -> int:
    """关闭扫描完成后自动打开的资源管理器窗口。

    【2026-11-26 根因】EPSON 扫完会自动打开输出文件夹（CabinetWClass），它盖在
    预览缩略图按钮上，set_focus 抢不过来，导致所有 click_input 都打到它身上：
    勾选清不掉、配置按钮点不动、文件保存设置图标没反应——全是这一个原因。
    """
    closed = 0
    for w in safe_windows():
        try:
            if w.class_name() in ("CabinetWClass", "ExploreWClass"):
                w.close()
                closed += 1
        except Exception:
            pass
    if closed:
        log(f"关闭了 {closed} 个资源管理器窗口（避免遮挡预览按钮）")
    return closed


def focus_window(win) -> bool:
    """后台模式下不激活窗口（空操作）；--foreground 时才置前。

    【2026-11-27 用户要求】默认不影响其他任务：不抢焦点、不动鼠标。
    消息级 BM_CLICK / TVM_SELECTITEM / WM_SETTEXT 都不需要窗口在前台。
    """
    if BACKGROUND:
        return True
    try:
        w = win.wrapper_object() if hasattr(win, "wrapper_object") else win
        w.set_focus()
        time.sleep(0.15)
        bring_to_front(w)
        return True
    except Exception:
        return False


def click_ctrl(spec, log_msg=None, focus_win=None) -> bool:
    """安全点击。

    【重要】不要在这里用 spec.parent() 去猜父窗口——对 EPSON 的图标按钮，
    parent() 调用会让后续 click_input 失效（实测：纯 wrapper.click_input 成功，
    经 parent() 后点击无反应）。改为由调用方显式传 focus_win（顶层窗口），
    或调用前已 focus_window 过则直接点。
    """
    try:
        if focus_win is not None:
            focus_window(focus_win)
        clickable = spec.wrapper_object() if hasattr(spec, "wrapper_object") else spec
        if BACKGROUND:
            # 后台点击：PostMessage BM_CLICK，不动鼠标、不抢焦点
            msg_click_handle(clickable.handle)
        else:
            clickable.click_input()
        op_pause()  # 每个操作后统一间隔 0.5s
        if log_msg:
            log(log_msg)
        return True
    except Exception as e:
        log(f"WARN 点击失败 {getattr(spec, 'handle', '?')}: {e}")
        return False


def list_controls(win, out_path: Path) -> None:
    def rec(w, f, depth=0):
        pad = "  " * depth
        if depth > 14:
            return
        try:
            ei = w.element_info
            r = ei.rectangle
            f.write(f"{pad}[{ei.control_type}] class={ei.class_name!r} "
                    f"name={ei.name!r} handle={ei.handle} "
                    f"rect=({r.left},{r.top},{r.right},{r.bottom}) vis={ei.visible}\n")
        except Exception:
            return
        try:
            for k in w.children():
                rec(k, f, depth + 1)
        except Exception:
            pass
    with out_path.open("w", encoding="utf-8") as f:
        rec(win, f)


def combos_by_current_name(win):
    """返回 [(当前名称, handle)]，名称即当前显示值（实测结论）。

    直接对 spec 取 window_text()/handle；不要多此一举 cb.wrapper_object()
    —— EPSON Scan 控件是动态重建的，spec 句柄此刻有效但取 wrapper 时常失效抛错。
    """
    out = []
    try:
        for cb in win.descendants(class_name="ComboBox"):
            try:
                if not cb.is_visible():
                    continue
                out.append((cb.window_text(), cb.handle))
            except Exception:
                pass
    except Exception:
        pass
    return out


def pick_combo(win, role_hints, candidates=None):
    """按当前名称提示找 ComboBox：优先精确/包含匹配角色提示。"""
    pairs = candidates if candidates is not None else combos_by_current_name(win)
    for hint in role_hints:
        for name, h in pairs:
            if name == hint:
                return h, name
    for hint in role_hints:
        for name, h in pairs:
            if hint and hint in name:
                return h, name
    return None, None


# 文件保存设置对话框「图像格式」ComboBox 的当前值总是这些格式名之一。
# 【2026-11-27 坑】不能拿目标格式当 hint 去找——当前值常是上次遗留的 JPEG，
# 找 TIFF 必然落空，结果格式没切成 TIFF、点「选项」开的还是 JPEG 插件框。
IMAGE_FORMAT_VALUES = ("TIFF (*.tif)", "JPEG (*.jpg)", "Multi-TIFF (*.tif)",
                       "PDF (*.pdf)", "BITMAP (*.bmp)", "全真数码影像技术II")


def find_image_format_combo(win):
    """按『当前值是已知图像格式名』定位图像格式 ComboBox（不依赖目标值）。"""
    for name, h in combos_by_current_name(win):
        if any(v in name for v in IMAGE_FORMAT_VALUES):
            return h, name
    return None, None


def combo_select_by_handle(h, value):
    w = Desktop(backend="win32").window(handle=h).wrapper_object()
    try:
        items = w.item_texts()
    except Exception:
        items = []
    if items:
        if value in items:
            w.select(value)
            op_pause()
            return True, f"selected {value!r}"
        # 模糊匹配
        for it in items:
            if value and (value in it or it in value):
                w.select(it)
                op_pause()
                return True, f"selected fuzzy {it!r} for {value!r}"
        return False, f"value {value!r} not in items {items!r}"
    # 无 items：后台模式用 WM_SETTEXT 设值（不碰键盘），前台模式才 type_keys
    try:
        if BACKGROUND:
            w.set_edit_text(value)
            op_pause()
            return True, f"set_edit_text {value!r} (无 items，未发 Enter)"
        w.type_keys(value + "{ENTER}")
        op_pause()
        return True, f"typed {value!r}"
    except Exception as e:
        return False, f"select failed: {e}"


def toggle_button(win, name_variants, want_on: bool, label: str):
    """开关 checkbox 类按钮。EPSON 的 USM/ICE 是 BS_CHECKBOX，用 BM_GETCHECK
    读状态（pywinauto 里是 get_check_state/is_checked，不是 get_toggle_state）。"""
    spec = find_ctrl_any(win, name_variants, cls="Button")
    if spec is None:
        log(f"WARN {label}: 按钮未找到 {name_variants}")
        return False
    try:
        w = spec.wrapper_object()
        # 优先 is_checked，其次 get_check_state
        if hasattr(w, "is_checked"):
            cur = bool(w.is_checked())
        elif hasattr(w, "get_check_state"):
            cur = bool(w.get_check_state())
        else:
            raise AttributeError("no check-state API")
        if cur != want_on:
            if BACKGROUND:
                msg_click_handle(w.handle)
            else:
                w.click_input()
            op_pause()
        log(f"{label}: {'开' if want_on else '关'} (was {'开' if cur else '关'})")
        return True
    except Exception as e:
        log(f"WARN {label}: 读状态失败({e})，跳过以免误切换（请手动确认）")
        return False


# ---------------------------------------------------------------- 主流程

def select_scanner(app, scanner_name: str):
    w = wait_window("EPSON Scan", "#32770", timeout=15)
    if w is None:
        raise RuntimeError("未找到 EPSON Scan 选择扫描仪对话框")
    spec = app.window(handle=w.handle)
    lb = spec.child_window(class_name="ListBox")
    if not lb.exists(timeout=1):
        return  # 已经进主窗口
    lbw = lb.wrapper_object()
    items = lbw.item_texts()
    log(f"扫描仪列表: {items}")
    target = scanner_name if scanner_name in items else next(
        (i for i in items if "V700" in i or "V750" in i), items[0] if items else None)
    if target is None:
        raise RuntimeError("没有可用扫描仪")
    lbw.select(target)
    op_pause()
    log(f"选择扫描仪: {target}")
    ok = spec.child_window(title="确定", class_name="Button")
    if BACKGROUND:
        msg_click_handle(ok.wrapper_object().handle)
    else:
        ok.click_input()
    op_pause()


def wait_main(app, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        for w in top_windows():
            try:
                if (w.window_text() == "EPSON Scan" and w.class_name() == "#32770"
                        and w.child_window(title="扫描(&S)", class_name="Button").exists(timeout=0.1)):
                    return w
            except Exception:
                pass
        time.sleep(0.5)
    # 兜底：只要标题是 EPSON Scan 且控件数足够
    for w in top_windows():
        try:
            if w.window_text() == "EPSON Scan" and len(w.children()) > 10:
                return w
        except Exception:
            pass
    raise RuntimeError("主窗口加载超时；可执行 --probe 查看控件树")


def refresh_main_if_stale(win):
    """文件保存设置确定后，EPSON 可能重建主窗口（旧句柄失效 → 控件全找不到）。
    这里检查 win 是否还找得到「扫描」按钮；找不到就按标题重新抓当前主窗口返回。
    找不到新窗口也返回原 win（保持原行为，由后续 find_ctrl_any 报错）。
    """
    try:
        if win.child_window(title="扫描(&S)", class_name="Button").exists(timeout=0.3):
            return win
    except Exception:
        pass
    # 旧句柄失效，按标题重新找含扫描按钮的 EPSON Scan 窗口
    for w in top_windows():
        try:
            if (w.window_text() == "EPSON Scan" and w.class_name() == "#32770"
                    and w.child_window(title="扫描(&S)", class_name="Button").exists(timeout=0.2)):
                log(f"主窗口句柄已刷新: {win.handle} -> {w.handle}")
                return w
        except Exception:
            continue
    return win


MODE_NAMES = ["全自动模式", "家庭模式", "办公模式", "专业模式"]
# 专业模式特征控件当前值（用于判断切模式后窗口重建完成）
PRO_FEATURES = ["胶片(带胶片支架)", "正片", "负片", "48-位全彩", "24-位全彩", "当前设置"]


def find_mode_combo(win):
    """定位模式下拉框，返回 (handle, 当前模式名)；找不到返回 (None, None)。

    首选：固定标签「模式(M):」后紧跟的 ComboBox（用户 2026-11-27 澄清标签恒定）。
    兜底：标签定位在部分模式失效（实测办公模式后找不到）——改为遍历所有 ComboBox，
    找选项列表同时含四种模式名的那个（此特征与当前模式无关，任何模式都成立）。
    """
    rows = _live_rows(win.handle)
    candidates = []

    r = _find_combo_after(rows, "模式")          # Static「模式(M):」后紧跟的 ComboBox
    if r is not None:
        candidates.append(r[0])

    # 兜底：任何选项列表含全部四种模式名的 ComboBox
    for row in rows:
        if row[1] != "ComboBox" or row[0] in candidates:
            continue
        try:
            items = Desktop(backend="win32").window(handle=int(row[0])).wrapper_object().item_texts()
            if all(any(m == it or m in it for it in items) for m in MODE_NAMES):
                candidates.append(row[0])
        except Exception:
            pass

    for h in candidates:
        try:
            idx = _sg(h, CB_GETCURSEL)
            items = Desktop(backend="win32").window(handle=int(h)).wrapper_object().item_texts()
            if 0 <= idx < len(items):
                return h, items[idx]
        except Exception:
            continue
    return None, None


def wait_window_rebuilt(want, old_handle=None, old_count=None, timeout=20):
    """切模式后等窗口重建：扫描按钮出现 + 模式框当前值回读为目标。返回新主窗口 spec。

    注意：CB_SETCURSEL 发出后窗口立即重建，旧 handle 上回读恒为 0（假 ok=False），
    必须在重建后的新窗口上回读。判定用「模式框当前值==want」，失败再退到特征控件。
    """
    end = time.time() + timeout
    while time.time() < end:
        for w in top_windows():
            try:
                if w.window_text() != "EPSON Scan" or w.class_name() != "#32770":
                    continue
                sp = Desktop(backend="win32").window(handle=w.handle)
                has_scan = sp.child_window(title="扫描(&S)", class_name="Button").exists(timeout=0.1)
                if not has_scan:
                    continue
                _, cur = find_mode_combo(sp)
                if cur == want:
                    return sp
                if cur is None and any(f in [n for n, _ in combos_by_current_name(sp)]
                                       for f in PRO_FEATURES):
                    return sp
            except Exception:
                pass
        time.sleep(0.4)
    return None


def ensure_professional_mode(win, cfg: RunConfig):
    """【流程第一步】窗口就位后立即把模式切到专业模式。

    为什么必须先做：四种模式的窗口控件完全不同（用户 2026-11-27 截图实证）——
    全自动模式只有「扫描/自定义」，没有「文档类型/胶片类型/分辨率/配置」等控件。
    不先切到专业模式，后面 apply_main_settings 找不到任何参数 ComboBox。

    实现：按固定标签「模式」定位下拉框 → 回读当前模式 → 已是目标则直接返回；
    否则 CB_SETCURSEL 选中（用户实测：选中即切换，无确认框）→ 等窗口重建 → 回读验证。

    attach 模式用户已手动切到专业时直接返回，无副作用。
    """
    want = getattr(cfg, "mode", "专业模式") or "专业模式"
    h, cur = find_mode_combo(win)
    if h is None:
        log("WARN 未找到「模式」下拉框（按固定标签定位），跳过模式切换")
        return win

    log(f"[模式] 当前={cur!r} 目标={want!r}")
    if cur == want:
        log("[模式] 已是目标模式，无需切换")
        return win

    ok, msg = _set_combo(h, want)
    log(f"[模式] 选择 {want!r} -> {msg} (ok={ok})")
    op_pause()
    # 用户澄清：切模式不弹确认框，无需点确定。
    # 【至少等 5 秒稳定】重建期间读到的都是中间态，必须等够了再判定。
    log(f"[模式] 等待 {MODE_SETTLE_SEC:.0f}s 稳定 ...")
    time.sleep(MODE_SETTLE_SEC)

    new_win = wait_window_rebuilt(want)
    if new_win is not None:
        log(f"[模式] 切换完成，新主窗口 handle={new_win.handle}")
        # 二次回读确认模式框当前值
        _, cur2 = find_mode_combo(new_win)
        log(f"[模式] 重建后回读={cur2!r}")
        return new_win
    log("WARN [模式] 等待重建超时，沿用原主窗口")
    return win


def apply_main_settings(win, cfg: RunConfig):
    pairs = combos_by_current_name(win)
    log(f"主窗口 ComboBox: {pairs}")

    if cfg.preset:
        h, cur = pick_combo(win, ["当前设置"], pairs)
        if h:
            ok, msg = combo_select_by_handle(h, cfg.preset)
            log(f"应用预设 {cfg.preset}: {msg}")
            if ok:
                return True
        log("WARN 预设未生效，回退逐项设置")

    # 【2026-11-27 修复】主窗口四项 ComboBox 一律按【左侧 Static 标签】定位，
    # 不再用「当前值」匹配 hints——当前值随上次保存状态变化（实测 反射/照片/1200），
    # 与角色名（胶片/正片/4800）毫无关系，靠当前值必然漏配。
    # 控件树实测标签：文稿类型(&Y): / 胶片类型(&F): / 图像类型(&I): / 分辨率(&L):
    # 模式已在 ensure_professional_mode()（流程第一步）切换，这里不再重复。
    rows = _live_rows(win.handle)
    settings = [
        ("文档类型", "文稿类型", cfg.document_type),
        ("胶片类型", "胶片类型", cfg.film_type),
        ("图像类型", "图像类型", cfg.image_type),
        ("分辨率", "分辨率", cfg.resolution),
    ]
    for label, static_label, value in settings:
        r = _find_combo_after(rows, static_label)
        if r is None:
            log(f"WARN {label}: 未找到 Static「{static_label}」后的 ComboBox")
            continue
        ok, msg = combo_select_by_handle(r[0], value)
        log(f"{label} (标签 {static_label}) -> {msg}")

    # 【2026-11-27 用户拍板】USM/ICE 已挪到 apply_main_usm_ice()，
    # 在文件保存设置之后、点扫描之前才设——此处只做四项 ComboBox。
    return False


def apply_main_usm_ice(win, cfg: RunConfig):
    """【后置设置】文件保存设置之后、点扫描之前设 USM/ICE。

    用户 2026-11-27 拍板的新顺序：USM/ICE 不放主设置阶段，而在预览选帧、
    文件保存设置都完成后再设——避免提前设被 EPSON 重置。
    - USM 开关 + 级别（低/中/高）
    - DIGITAL ICE Technology 开关 + 模式下拉（勾选后可见，如「速度」/「质量」）
    """
    log("=== 后置：USM / DIGITAL ICE 设置 ===")
    usm = getattr(cfg, "usm", False)
    level = getattr(cfg, "usm_level", "低")
    ice = getattr(cfg, "ice", False)
    ice_mode = getattr(cfg, "ice_mode", "") or ""

    toggle_button(win, ["USM锐化(&K)", "USM锐化(K)", "USM锐化"], usm, "USM锐化")
    op_pause()
    if usm:
        h, cur = pick_combo(win, [level, "低", "中", "高"], combos_by_current_name(win))
        if h:
            ok, msg = combo_select_by_handle(h, level)
            log(f"USM 级别 -> {msg}")
        else:
            log("WARN USM 级别下拉未找到")

    toggle_button(win, ["DI&GITAL ICE Technology", "DIGITAL ICE Technology"], ice, "DIGITAL ICE")
    op_pause()
    if ice and ice_mode:
        # ICE 勾选后其「模式」下拉（当前值含 DIGITAL ICE Technology）才可见
        time.sleep(0.4)
        h, cur = pick_combo(win, ["DIGITAL ICE Technology", "速度", "质量"], combos_by_current_name(win))
        if h:
            ok, msg = combo_select_by_handle(h, ice_mode)
            log(f"ICE 模式 -> {msg}")
        else:
            log("WARN ICE 模式下拉未找到（勾选 ICE 后应可见）")


def open_filesettings(win):
    """点击扫描按钮右侧相邻的无标题图标按钮，打开"文件保存设置"。

    实测（2026-11-24，中文 EPSON Scan 3.9）：扫描按钮 rect=(609,1081,750,1141)，
    文件保存设置图标在其右侧 (755,1096,800,1141)，预览按钮在左侧 (411..576)。
    """
    items = []
    def rec(w, depth=0):
        if depth > 12:
            return
        try:
            ei = w.element_info
            r = ei.rectangle
            # pywinauto 0.6.9: element_info.class_name 是属性，不是方法（加括号会抛
            # 'str' object is not callable 被 except 吞掉，导致 items 全空）
            items.append(dict(cls=ei.class_name, name=ei.name, handle=ei.handle,
                              rect=(r.left, r.top, r.right, r.bottom), vis=ei.visible))
        except Exception:
            return
        try:
            for k in w.children():
                rec(k, depth + 1)
        except Exception:
            pass
    rec(win)
    scans = [i for i in items if i["cls"] == "Button" and "扫描" in i["name"] and i["vis"]]
    if not scans:
        raise RuntimeError("找不到扫描按钮，无法定位文件保存设置图标")
    sr = scans[0]["rect"]
    # 图标在扫描按钮【右侧】紧邻，top 对齐（rect[1] 相近），左边界 >= 扫描按钮右边界
    cands = [i for i in items if i["cls"] == "Button" and not i["name"] and i["vis"]
             and abs(i["rect"][1] - sr[1]) < 45 and i["rect"][0] >= sr[2] - 5
             and (i["rect"][2] - i["rect"][0]) < 80]
    cands.sort(key=lambda i: i["rect"][0] - sr[2])  # 离扫描按钮最近的优先
    if not cands:
        raise RuntimeError("找不到文件保存设置图标按钮（扫描按钮右侧无名按钮）")
    icon_h = cands[0]["handle"]
    log(f"点击文件保存设置图标 handle={icon_h}")
    # 自绘图标按钮 BM_CLICK 偶发第一次不响应（实测诊断时手动点一次就弹窗），
    # 消息级重试最多 3 次，每次不碰鼠标/不抢焦点。
    focus_window(win)
    icon = Desktop(backend="win32").window(handle=icon_h)
    dlg = None
    for attempt in range(1, 4):
        click_ctrl(icon, f"文件保存设置图标点击 {attempt}/3", focus_win=win)
        time.sleep(0.8)
        dlg = wait_window("文件保存设置", contains=True, timeout=3)
        if dlg is None:
            dlg = wait_window("保存设置", contains=True, timeout=2)
        if dlg is not None:
            log(f"文件保存设置窗口出现（第 {attempt} 次点击）")
            break
        log(f"WARN 图标点击 {attempt}/3 未弹窗，重试")
    if dlg is None:
        raise RuntimeError(
            "文件保存设置窗口未出现。图标已点击 3 次仍无响应——本机扫描仪需"
            "【物理断电重启后第一次打开 EPSON Scan】才正常；请按此重启后再试。"
            "（若已冷启动仍失败，可 --probe 查看控件树确认图标位置）"
        )
    return dlg


def close_dialog(dlg) -> bool:
    """正常关闭一个对话框：只点消息级「取消」/「关闭」，绝不 WM_CLOSE、绝不 taskkill。
    【铁律 2026-11-27】WM_CLOSE 强关「文件保存设置/浏览文件夹」等子对话框会连带
    杀掉 EPSON Scan 主窗口（同一 TWAIN 会话）。必须用「取消」正常退出。"""
    import win32gui
    if dlg is None:
        return True
    try:
        h = dlg.handle if hasattr(dlg, "handle") else dlg.wrapper_object().handle
    except Exception:
        return False
    for _ in range(5):
        if not win32gui.IsWindow(h):
            return True
        try:
            b = find_ctrl_any(dlg, ["取消", "关闭", "Cancel"], cls="Button")
            if b:
                msg_click_handle(b.wrapper_object().handle if hasattr(b, "wrapper_object") else b.handle)
        except Exception:
            pass
        time.sleep(0.5)
    left = win32gui.IsWindow(h)
    if left:
        log("WARN 对话框点「取消」5 次仍未关闭；保留原状，绝不 WM_CLOSE 强关（会杀主窗口）")
    return not left


# ---------------------------------------------------------------- 配置对话框底层工具（流程方案驱动）
# Win32 消息常量（均不带指针，跨进程安全）
BM_GETCHECK = 0x00F0
CB_GETCOUNT = 0x0146
CB_GETCURSEL = 0x0147
CB_SETCURSEL = 0x014E
TBM_GETPOS = 0x0400
TBM_GETRANGEMIN = 0x0401
TBM_GETRANGEMAX = 0x0402
TBM_SETPOS = 0x0405
CONFIRM_KEYWORDS = ("初始化", "继续吗", "改变预览模式")


def _sg(h, msg, wp=0, lp=0):
    import win32gui
    return win32gui.SendMessage(int(h), msg, wp, lp)


def _norm_cfg(s):
    import re
    return re.sub(r"[\s&()（）:：/]", "", str(s)).lower()


def _live_rows(hwnd):
    """(h, cls, txt, vis, en, rect) —— 枚举配置框所有子控件（非激活页 vis=0 但 handle 有效）"""
    import win32gui
    out = []

    def cb(h, _):
        try:
            out.append((h, win32gui.GetClassName(h), win32gui.GetWindowText(h),
                        bool(win32gui.IsWindowVisible(h)), bool(win32gui.IsWindowEnabled(h)),
                        win32gui.GetWindowRect(h)))
        except Exception:
            pass
        return True

    win32gui.EnumChildWindows(int(hwnd), cb, None)
    return out


def _find_btn(rows, name):
    n = _norm_cfg(name)
    for r in rows:
        if r[1] == "Button" and (n in _norm_cfg(r[2]) or _norm_cfg(r[2]) in n):
            return r
    return None


def _find_combo_after(rows, label):
    """空文本 ComboBox：找标签 Static 后紧跟的第一个 ComboBox"""
    n = _norm_cfg(label)
    for i, r in enumerate(rows):
        if r[1] == "Static" and n in _norm_cfg(r[2]):
            for r2 in rows[i + 1:]:
                if r2[1] == "ComboBox":
                    return r2
                if r2[1] == "Static" and _norm_cfg(r2[2]):
                    break
    return None


def _find_slider(rows):
    sl = [r for r in rows if r[1] == "msctls_trackbar32"]
    vis = [r for r in sl if r[3]]
    return vis[0] if len(vis) == 1 else (sl[0] if len(sl) == 1 else None)


def _dismiss_confirm():
    """点掉「改变预览模式会初始化设置」确认框——标题也是 EPSON Scan，靠正文关键词识别。"""
    d = Desktop(backend="win32")
    n = 0
    for w in safe_windows():
        try:
            if w.class_name() != "#32770":
                continue
            rows = _live_rows(w.handle)
            body = " ".join(r[2] for r in rows if r[1] in ("Static", "Button"))
            if not any(k in body for k in CONFIRM_KEYWORDS):
                continue
            ok = _find_btn(rows, "确定")
            if ok:
                msg_click_handle(ok[0])
                n += 1
                log(f"  [确认框] 已点「确定」：{body[:40]}")
        except Exception:
            pass
    return n


def _set_check(h, want):
    cur = bool(_sg(h, BM_GETCHECK))
    if cur != want:
        msg_click_handle(h)
    after = bool(_sg(h, BM_GETCHECK))
    return after == want, f"{int(cur)}->{int(after)}"


def _set_radio(h, want_on):
    if want_on:
        msg_click_handle(h)
    after = bool(_sg(h, BM_GETCHECK))
    return after == want_on, f"->checked={int(after)}"


def _set_combo(h, want):
    try:
        w = Desktop(backend="win32").window(handle=int(h)).wrapper_object()
        items = w.item_texts()
        pick = next((it for it in items if _norm_cfg(it) == _norm_cfg(want)), None)
        if pick is None:
            pick = next((it for it in items if _norm_cfg(want) in _norm_cfg(it)
                         or _norm_cfg(it) in _norm_cfg(want)), None)
        if pick is None:
            return False, f"无匹配项 {want!r} in {items!r}"
        w.select(pick)
        idx = items.index(pick)
        after = _sg(h, CB_GETCURSEL)
        return after == idx, f"cur->{after} {pick!r}"
    except Exception as ex:
        return False, f"{type(ex).__name__}: {ex}"


def _set_slider(h, mode):
    mn, mx, cur = _sg(h, TBM_GETRANGEMIN), _sg(h, TBM_GETRANGEMAX), _sg(h, TBM_GETPOS)
    if mode == "min":
        target = mn
    elif mode == "max":
        target = mx
    elif isinstance(mode, float):
        target = int(round(mn + mode * (mx - mn)))
    else:
        target = int(mode)
    _sg(h, TBM_SETPOS, 1, target)
    after = _sg(h, TBM_GETPOS)
    return abs(after - target) <= 1, f"range {mn}-{mx} cur {cur}->{after} target {target}"


def apply_config_items(cd, items: dict):
    """对已打开的配置对话框按 {显示名: 值} 逐项设置（彩色页为主）。
    值的类型决定操作：bool→check/radio（按标签是否含色彩控制/ICM/无色彩校正判 radio），
    str→combo，float/int→slider。返回 (ok, total)。"""
    rows = _live_rows(cd.handle)
    ok = total = 0
    radio_names = ("色彩控制", "icm", "无色彩校正")
    for label, want in items.items():
        total += 1
        is_radio = any(_norm_cfg(label) == _norm_cfg(rn) or _norm_cfg(rn) in _norm_cfg(label)
                       for rn in radio_names)
        if isinstance(want, str):
            r = _find_combo_after(rows, label)
            good, detail = _set_combo(r[0], want) if r else (False, "ComboBox 未找到")
        elif isinstance(want, (int, float)) and not isinstance(want, bool):
            r = _find_slider(rows)
            good, detail = _set_slider(r[0], want) if r else (False, "Slider 未找到")
        else:
            r = _find_btn(rows, label)
            if not r:
                good, detail = False, "按钮未找到"
            else:
                good, detail = (_set_radio(r[0], want) if is_radio else _set_check(r[0], want))
        _dismiss_confirm()  # 切色彩模式会弹确认框
        log(f"  配置项 {label:<16} {'OK' if good else 'FAIL':<6} {detail}")
        ok += good
    return ok, total


def _uia_tab_items(cfg_hwnd):
    """UIA 读配置对话框 Tab 页名+中心坐标（COM 方式，不跨进程发指针消息）。
    返回 [(页名, cx, cy)]。"""
    out = []
    try:
        d = Desktop(backend="uia")
        dlg = d.window(handle=cfg_hwnd)
        for tab in dlg.descendants(control_type="Tab"):
            for it in tab.descendants(control_type="TabItem"):
                try:
                    r = it.rectangle()
                    out.append((it.window_text(), (r.left + r.right) // 2, (r.top + r.bottom) // 2))
                except Exception:
                    pass
        if out:
            return out
    except Exception as ex:
        log(f"WARN UIA 读 Tab 失败: {ex}")
    return out


def _switch_config_tab(cfg_hwnd, page_name):
    """真实鼠标点标签切到指定页（整数坐标，不跨进程指针消息，已验证安全）。
    切页会占鼠标一下；返回是否点到。"""
    from pywinauto import mouse as _mouse
    for name, cx, cy in _uia_tab_items(cfg_hwnd):
        if name == page_name or _norm_cfg(name) == _norm_cfg(page_name):
            # pywinauto mouse.click 首位是 button，坐标必须走 coords= 关键字，
            # 否则整数被当 button 调用 .lower() -> AttributeError 崩溃。
            _mouse.click(coords=(cx, cy))
            time.sleep(0.5)
            return True
    return False


def apply_config_pages(cd, pages: dict):
    """按 {页名: {控件名: 值}} 逐页设置配置对话框。

    每页：UIA 点标签切换到该页 → 用 labels 定位控件（切页后仅当前页控件 vis=True，
    _find_slider/_find_combo_after 会自动选中当前页那个）→ 逐项设值并回读。
    值类型：bool→check/radio，str→combo，float/int→slider(比例或档位)。
    """
    rows = _live_rows(cd.handle)
    ok = total = 0
    for page, items in pages.items():
        if not _switch_config_tab(cd.handle, page):
            log(f"WARN 配置页 {page!r} 切换失败（UIA 未找到标签），跳过该页")
            continue
        rows = _live_rows(cd.handle)
        log(f"配置页 [{page}] 开始设置 {list(items)}")
        for label, want in items.items():
            total += 1
            is_radio = any(_norm_cfg(rn) in _norm_cfg(label) for rn in ("色彩控制", "icm", "无色彩校正"))
            if isinstance(want, str):
                r = _find_combo_after(rows, label)
                good, detail = _set_combo(r[0], want) if r else (False, "ComboBox 未找到")
            elif isinstance(want, (int, float)) and not isinstance(want, bool):
                r = _find_slider(rows)
                good, detail = _set_slider(r[0], want) if r else (False, "Slider 未找到")
            else:
                r = _find_btn(rows, label)
                good, detail = (_set_radio(r[0], want) if is_radio else _set_check(r[0], want)) if r else (False, "按钮未找到")
            log(f"    {label:<16} {'OK' if good else 'FAIL':<6} {detail}")
            ok += good
        _dismiss_confirm()
    return ok, total


def apply_config_dialog(win, cfg: RunConfig):
    """打开主窗口「配置(&O)...」对话框，设置 rank 管线的色彩/曝光选项。

    实测控件（2026-11-26 dump）：
      色彩控制(&C) / &ICM / 无色彩校正(&N)（互斥单选，当前=无色彩校正）
      连续自动曝光(&E)、显示 Gamma(1.8/2.2)、来源(爱普生标准)、目标(sRGB)
    rank 管线：无色彩校正 + 连续自动曝光关（与历史 48bit TIFF 榜单同口径）。
    config.color_mode 可覆盖为 '色彩控制' / 'ICM' / '无色彩校正'。
    """
    opt = find_ctrl_any(win, ["配置(&O)...", "配置(O)...", "配置...", "配置"], cls="Button")
    if opt is None:
        log("WARN 找不到「配置」按钮，跳过")
        return False
    focus_window(win)
    click_ctrl(opt, "打开主窗口「配置」", focus_win=win)
    # 轮询等待对话框出现（最多 10s），标题精确/包含匹配都接受
    cd = None
    end = time.time() + 10
    while time.time() < end and cd is None:
        for w in safe_windows():
            try:
                t = w.window_text().strip()
                if (w.class_name() == "#32770" and w.handle != win.handle
                        and t == "配置"):
                    cd = Desktop(backend="win32").window(handle=w.handle)
                    break
            except Exception:
                continue
        if cd is None:
            time.sleep(0.3)
    if cd is None:
        log("WARN 配置对话框未出现，跳过")
        return False
    log(f"配置对话框就绪 handle={cd.handle}")

    focus_window(cd)
    pages = getattr(cfg, "config_pages", None)
    items = getattr(cfg, "config_dialog_items", None)
    if pages:
        # 【按页驱动】预览页+彩色页分别设置（各页滑块按 vis 区分）
        log(f"配置对话框 -> 按页方案 {list(pages)}")
        apply_config_pages(cd, pages)
    elif items:
        # 【流程方案驱动】按 JSON 逐项设置彩色页（含 Gamma/曝光滑块等）
        log(f"配置对话框 -> 方案项 {list(items)}")
        apply_config_items(cd, items)
    else:
        # 旧行为（向后兼容）：色彩控制三选一（互斥 radio）
        want_color = getattr(cfg, "color_mode", "无色彩校正")
        for name in [want_color, "无色彩校正(&N)", "色彩控制(&C)", "&ICM"]:
            rb = find_ctrl_any(cd, [name], cls="Button")
            if rb is None:
                continue
            try:
                if not rb.is_checked():
                    click_ctrl(rb, focus_win=cd)
                    log(f"配置 -> {name}")
                break
            except Exception:
                pass

        # 连续自动曝光：rank 要求关
        ae = find_ctrl_any(cd, ["连续自动曝光(&E)", "连续自动曝光"], cls="Button")
        if ae:
            try:
                if ae.is_checked():
                    click_ctrl(ae, focus_win=cd)
                    log("配置 -> 连续自动曝光: 关")
            except Exception:
                pass

    _dismiss_confirm()  # 点确定前先清确认框
    ok = find_ctrl_any(cd, ["确定"], cls="Button")
    if ok:
        click_ctrl(ok, "配置 -> 确定", focus_win=cd)
    _dismiss_confirm()  # 保存时可能再弹
    return True


def apply_tiff_plugin_options(dlg, cfg: RunConfig):
    """【专项测试】点「选项(&O)...」打开 TIFF 插件设置子对话框，读取/设置子选项，
    再点确定关闭。实测子对话框标题='爱普生 TIFF Plug-in 设置'，控件：
      位元组顺序: &Windows / &Macintosh（radio）
      压缩: 彩色/灰度(&C): 值(如'无')、黑白(&B): 值(如'无')（ComboBox）
      嵌入ICC特性文件（checkbox）
    rank 管线要求：Windows 字节序 + 不压缩 + 嵌入ICC（如需可在此设）。
    """
    opt = find_ctrl_any(dlg, ["选项(&O)...", "选项(O)...", "选项...", "选项"], cls="Button")
    if opt is None:
        log("WARN TIFF「选项」按钮未找到")
        return False
    focus_window(dlg)
    click_ctrl(opt, "文件保存设置 -> 选项(&O)...（打开 TIFF 插件设置）", focus_win=dlg)
    time.sleep(0.8)
    sub = None
    end = time.time() + 8
    while time.time() < end and sub is None:
        for w in safe_windows():
            try:
                if (w.class_name() == "#32770" and w.handle != dlg.handle
                        and ("TIFF" in w.window_text() or "Plug-in" in w.window_text())):
                    sub = Desktop(backend="win32").window(handle=w.handle)
                    break
            except Exception:
                continue
        if sub is None:
            time.sleep(0.3)
    if sub is None:
        log("WARN TIFF 插件设置子对话框未出现")
        return False
    log(f"TIFF 插件设置子对话框: {sub.window_text()!r} handle={sub.handle}")
    focus_window(sub)

    # 读取全部可见控件状态
    for c in sub.descendants():
        try:
            if not c.is_visible():
                continue
            t = c.window_text().strip()
            ct = c.element_info.control_type
            if not t:
                continue
            extra = ""
            if hasattr(c, "is_checked"):
                extra = f" checked={c.is_checked()}"
            if ct == "ComboBox":
                try:
                    extra = f" items={c.item_texts()}"
                except Exception:
                    pass
            log(f"  TIFF子选项: {ct:10s} {t!r}{extra}")
        except Exception:
            pass

    # 【流程方案驱动】按 cfg.tiff_options 设置；未给则沿用旧硬编码（Windows+不压缩+嵌ICC）
    topt = getattr(cfg, "tiff_options", None) or {}
    want_order = topt.get("位元组顺序", "Windows")
    want_icc = topt.get("嵌入ICC", True)
    rows = _live_rows(sub.handle)
    # 位元组顺序 radio
    order_rb = find_ctrl_any(sub, [f"&{want_order}", want_order], cls="Button")
    if order_rb:
        try:
            if not order_rb.is_checked():
                click_ctrl(order_rb, f"TIFF子选项 -> 位元组顺序 {want_order}", focus_win=sub)
        except Exception as ex:
            log(f"WARN 位元组顺序设置失败: {ex}")
    # 压缩：彩色/灰度 与 黑白 两个 ComboBox（标签后紧跟）
    for key, label in (("彩色灰度压缩", "彩色/灰度"), ("黑白压缩", "黑白")):
        want_c = topt.get(key)
        if not want_c:
            continue
        cb = _find_combo_after(rows, label)
        if cb:
            good, detail = _set_combo(cb[0], want_c)
            log(f"  TIFF子选项 {key:<12} {'OK' if good else 'FAIL':<6} {detail}")
        else:
            log(f"  WARN TIFF子选项 {key} ComboBox 未找到")
    # 嵌入 ICC 特性文件 checkbox
    icc = find_ctrl_any(sub, ["嵌入ICC特性文件", "嵌入 ICC 特性文件"], cls="Button")
    if icc:
        try:
            cur = bool(icc.is_checked())
            if cur != want_icc:
                click_ctrl(icc, f"TIFF子选项 -> 嵌入ICC {'开' if want_icc else '关'}", focus_win=sub)
        except Exception as ex:
            log(f"WARN 嵌入ICC设置失败: {ex}")

    okb = find_ctrl_any(sub, ["确定"], cls="Button")
    if okb:
        click_ctrl(okb, "TIFF子选项 -> 确定（关闭）", focus_win=sub)
    return True


def apply_jpeg_plugin_options(dlg, cfg: RunConfig):
    """点「选项(&O)...」进「爱普生 JPEG Plug-in 设置」子对话框，确保三项：
      压缩等级 = jpeg_quality（1=最高，UI 左"高质量"右"低质量"，范围 1-100）
      编码     = 标准（非逐行）
      嵌入ICC  = 勾选
    实测控件（jpeg_plugin_dump.txt）：
      Edit(空文本, h) = 压缩等级值；msctls_trackbar32 'Slider1' = 质量滑块
      Button '标准(&S)' / '逐行(&P)'（radio）；Button '嵌入ICC特性文件'（checkbox）
    全程消息级（BM_CLICK / TBM_SETPOS），不占鼠标。
    压缩等级只能点滑块（TBM_SETPOS），那个 Edit 是自绘显示框、不接受文本输入。
    """
    opt = find_ctrl_any(dlg, ["选项(&O)...", "选项(O)...", "选项...", "选项"], cls="Button")
    if opt is None:
        log("WARN JPEG「选项」按钮未找到")
        return False
    click_ctrl(opt, "文件保存设置 -> 选项(&O)...（打开 JPEG 插件设置）", focus_win=dlg)
    time.sleep(0.6)
    sub = None
    end = time.time() + 8
    while time.time() < end and sub is None:
        for w in safe_windows():
            try:
                if (w.class_name() == "#32770" and w.handle != dlg.handle
                        and "JPEG" in w.window_text() and "Plug-in" in w.window_text()):
                    sub = Desktop(backend="win32").window(handle=w.handle)
                    break
            except Exception:
                continue
        if sub is None:
            time.sleep(0.3)
    if sub is None:
        log("WARN JPEG 插件设置子对话框未出现")
        return False
    log(f"JPEG 插件设置子对话框: {sub.window_text()!r} handle={sub.handle}")
    rows = _live_rows(sub.handle)

    want_q = getattr(cfg, "jpeg_quality", None) or 1
    # 【2026-11-27 实测】压缩等级真值存在 msctls_trackbar32 'Slider1' 的位置里
    # （范围 1-100，1=最高质量=最左）。那个无文本 Edit 是自绘显示框，
    # GetWindowText/WM_SETTEXT 都读不到值（实测恒为空），不能拿它当判据。
    slider = next((r for r in rows if r[1] == "msctls_trackbar32"), None)
    if slider:
        # 【2026-11-27 用户确认】压缩等级只能点滑块，不能往 Edit 输入；
        # 只发 TBM_SETPOS 设滑块位置，绝不 WM_SETTEXT 那个自绘 Edit。
        good, detail = _set_slider(slider[0], want_q)
        log(f"JPEG 压缩等级 -> {'OK' if good else 'FAIL'} {detail}")
    else:
        log("WARN JPEG 压缩等级 Slider1 未找到")

    # 编码：标准 radio 开、逐行 radio 关
    std = next((r for r in rows if r[1] == "Button" and "标准" in r[2]), None)
    prog = next((r for r in rows if r[1] == "Button" and "逐行" in r[2]), None)
    if std:
        good, detail = _set_radio(std[0], True)
        log(f"JPEG 编码 标准 -> {'OK' if good else 'FAIL'} {detail}")
    else:
        log("WARN JPEG「标准」radio 未找到")
    if prog:
        good, detail = _set_check(prog[0], False)
        # radio 互斥，设标准后逐行应自动关；这里只记录，不强行点击（避免破坏互斥）
        log(f"JPEG 编码 逐行 -> {'OK' if good else 'FAIL'} {detail}")

    # 嵌入 ICC 特性文件 checkbox
    icc = next((r for r in rows if r[1] == "Button" and "嵌入ICC" in r[2].replace(" ", "")), None)
    if icc:
        good, detail = _set_check(icc[0], True)
        log(f"JPEG 嵌入ICC -> {'OK' if good else 'FAIL'} {detail}")
    else:
        log("WARN JPEG「嵌入ICC特性文件」未找到")

    okb = find_ctrl_any(sub, ["确定"], cls="Button")
    if okb:
        click_ctrl(okb, "JPEG 插件设置 -> 确定（关闭）", focus_win=sub)
    return True


def apply_open_folder_checkbox(dlg, want: bool):
    """【流程方案驱动】把「扫描后打开图像文件夹(&F)」设为目标态（只点必要的次数）。"""
    spec = find_ctrl_any(dlg, ["扫描后打开图像文件夹(&F)", "扫描后打开图像文件夹"], cls="Button")
    if spec is None:
        log("WARN「扫描后打开图像文件夹」按钮未找到")
        return False
    try:
        w = spec.wrapper_object()
        cur = bool(w.is_checked())
        if cur != want:
            msg_click_handle(w.handle)
            after = bool(w.is_checked())
            log(f"扫描后打开文件夹: {'勾' if cur else '未勾'} -> {'勾' if after else '未勾'}"
                f" {'OK' if after == want else 'FAIL'}")
            return after == want
        log(f"扫描后打开文件夹: 已是 {'勾' if cur else '未勾'}（保持）")
        return True
    except Exception as ex:
        log(f"WARN 打开文件夹勾选设置失败: {ex}")
        return False


def test_open_folder_checkbox(dlg, cfg: RunConfig):
    """【专项测试】「扫描后打开图像文件夹(&F)」能否正常打勾和取消。
    流程：读当前态 -> 取消 -> 回读确认 -> 重新勾上 -> 回读确认（最终保持勾选，
    这样扫描完成会自动打开文件夹作为完成信号）。"""
    spec = find_ctrl_any(dlg, ["扫描后打开图像文件夹(&F)", "扫描后打开图像文件夹"], cls="Button")
    if spec is None:
        log("WARN「扫描后打开图像文件夹」按钮未找到")
        return False
    try:
        w = spec.wrapper_object()
        cur = bool(w.is_checked())
        log(f"打开文件夹勾选-初始: {'勾' if cur else '未勾'}")
        # 取消
        if cur:
            if BACKGROUND:
                msg_click_handle(w.handle)
            else:
                w.click_input()
            op_pause()
            cur2 = bool(w.is_checked())
            log(f"打开文件夹勾选-点一次: {'勾' if cur2 else '未勾'} {'✓取消成功' if not cur2 else '✗未取消'}")
        # 重新勾上（最终保持勾选）
        if not w.is_checked():
            if BACKGROUND:
                msg_click_handle(w.handle)
            else:
                w.click_input()
            op_pause()
            cur3 = bool(w.is_checked())
            log(f"打开文件夹勾选-再点一次: {'勾' if cur3 else '未勾'} {'✓勾选成功' if cur3 else '✗未勾上'}")
        return True
    except Exception as ex:
        log(f"WARN 打开文件夹勾选测试失败: {ex}")
        return False


# ---------------------------------------------------------------- 浏览文件夹（SHBrowseForFolder）

# 【2026-11-27 用户拍板的方法，替换此前全部失败的方案】
#   旧方案踩坑记录：
#     * 浏览对话框里那个 Edit 是 vis=False/en=False 的只读回显框，WM_SETTEXT 无效；
#     * BFFM_SETSELECTION 发消息后树不刷新，只读框也不变；
#     * 对 SysTreeView32 直接 type_keys 绝对路径，字符被当成树内增量搜索，
#       结果选中了 D:\DiskGenius_WinPE（这就是上轮"回读变 DiskGenius_WinPE"的根因）。
#   正解（用户口述）：
#     树里找到「本地磁盘 C: / 本地磁盘 (D:)」这类大目录 → 点名称前箭头展开/收回
#     → 单击盘符名称（蓝色背景即选中）→ 点「新建文件夹(&M)」按钮
#     → 此时树里出现可编辑输入框，直接输入文件夹名 → 模拟 Enter 即创建成功
#     → 需要子目录就对新建的节点重复同流程 → 点「确定」关闭浏览窗口
#     → 回到「文件保存设置」，路径框显示的就是末级文件夹名（= 保存路径末级）。
#   树节点是 pywinauto 的 _treeview_element：文本用 text()，不是 window_text()；
#   矩形用 client_rect()，展开用 expand()，选中用 select()。


def _t(item) -> str:
    """读树节点文本（_treeview_element 没有 window_text()）。"""
    try:
        return item.text()
    except Exception:
        return ""


def _norm(s) -> str:
    """归一化节点文本：去空白 + 全角括号转半角 + 小写。"""
    return "".join(str(s).split()).replace("（", "(").replace("）", ")").lower()


def _tree_expand(item) -> bool:
    """展开节点（等价于点名称前的右箭头），等 shell 命名空间把子项填进来。"""
    try:
        item.expand()
        time.sleep(0.35)
        return True
    except Exception:
        return False


def _tree_children(item, retries=3):
    """读直接子节点。SHBrowseForFolder 是延迟加载，展开后要重试。"""
    for _ in range(retries):
        _tree_expand(item)
        try:
            kids = item.children()
        except Exception:
            kids = []
        if kids:
            return kids
        time.sleep(0.35)
    return []


def _tree_match(kids, name):
    """在子节点里找 name：只做归一化精确匹配。

    【2026-11-27 bug 修复】旧的"双向包含匹配"会把 `A_爱普生扫描_新建验证`
    误匹配到已存在的 `A_爱普生扫描`（互为子串），选中错误文件夹 → 路径框写回
    旧名。文件夹选择必须精确，禁止包含匹配。
    """
    n = _norm(name)
    for k in kids:
        if _norm(_t(k)) == n:
            return k
    return None


def _tree_locate_drive(tv, drive: str):
    """定位「本地磁盘 (X:)」节点：桌面 -> 此电脑 -> 本地磁盘 (X:)，失败再 BFS 兜底。"""
    key = "(" + drive.rstrip(":").lower() + ":)"  # '(d:)'
    roots = tv.roots()
    desk = next((r for r in roots if _norm(_t(r)) == _norm("桌面")), None)
    if desk is not None:
        pc = _tree_match(_tree_children(desk), "此电脑")
        if pc is None:
            pc = _tree_match(_tree_children(desk), "我的电脑")
        if pc is not None:
            for k in _tree_children(pc):
                if key in _norm(_t(k)):
                    return k
    # 兜底：从所有根向下找 2 层
    for r in roots:
        if key in _norm(_t(r)):
            return r
        for c in _tree_children(r, retries=1):
            if key in _norm(_t(c)):
                return c
            for g in _tree_children(c, retries=1):
                if key in _norm(_t(g)):
                    return g
    return None


def _tree_click_bg(tv, item) -> bool:
    """【2026-11-27 定版】后台消息级"单击节点文字"：直接给树控件投递
    WM_LBUTTONDOWN/WM_LBUTTONUP 到节点文字中心。

    为什么不能只发 TVM_SELECTITEM：SHBrowseForFolder 的"文件夹(F):"回显框和
    确定按钮的 pidl 靠 **TVN_SELCHANGED 通知** 更新；没有焦点的树会忽略
    TVM_SELECTITEM / 或只改内部 caret 而不发通知 → 现象就是"新建成功但没选中，
    点确定路径框还是旧的，得再开一次浏览重新选"。投递鼠标消息走的是树的正常
    消息路径，会发 TVN_SELCHANGED，且不移动真实鼠标、不抢前台。
    """
    import win32gui
    import win32con
    try:
        item.ensure_visible()          # TVM_ENSUREVISIBLE，纯消息，不抢焦点
        time.sleep(0.15)
        rc = item.client_rect()        # 相对树客户区
        if rc is None:
            return False
        x = int((rc.left + rc.right) // 2)
        y = int((rc.top + rc.bottom) // 2)
        lp = (y << 16) | (x & 0xFFFF)
        h = int(tv.handle)
        win32gui.SendMessage(h, win32con.WM_MOUSEMOVE, 0, lp)
        win32gui.SendMessage(h, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        win32gui.SendMessage(h, win32con.WM_LBUTTONUP, 0, lp)
        op_pause()
        return True
    except Exception as ex:
        log(f"  WARN 消息级单击树节点失败: {ex}")
        return False


def _tree_select_click(tv, item, bd) -> bool:
    """选中树节点（蓝底）。

    【2026-11-27 实测】给树控件投递 WM_LBUTTONDOWN/UP（_tree_click_bg）会点偏
    （回显跳到「桌面/wallpaper」，还会移动真实鼠标），已废弃。
    后台模式用 TVM_SELECTITEM 设 caret；真正的"路径写回"判据是点确定后
    「文件保存设置」路径框——若不对，apply_filesettings 会自动重开浏览重新选中。
    """
    if not BACKGROUND:
        try:
            focus_window(bd)
            item.click_input(where="text")
            op_pause()
            if item.is_selected():
                return True
        except Exception:
            pass
    try:
        import win32gui
        TVM_SELECTITEM = 0x110B
        TVGN_CARET = 0x000C
        win32gui.SendMessage(int(tv.handle), TVM_SELECTITEM, TVGN_CARET, int(item.elem))
        op_pause()
        # 后台模式：发完即认定成功，不再读 is_selected()/走 item.select()——
        # pywinauto 的 item.select() 内部 set_focus() 会抢前台（实测鼠标被带偏）。
        # 路径是否真的写对由 apply_filesettings 的路径框校验兜底。
        if BACKGROUND:
            return True
        if item.is_selected():
            return True
    except Exception:
        pass
    if not BACKGROUND:
        try:
            item.select()
            op_pause()
            return True
        except Exception:
            pass
    return False


def _browse_hidden_edit(bd) -> str:
    """浏览对话框隐藏只读 Edit = 当前选中文件夹的显示名（关键回读判据）。"""
    for e in bd.descendants(class_name="Edit"):
        try:
            if not e.is_visible():
                return e.window_text().strip()
        except Exception:
            pass
    return ""


def _browse_new_folder(bd, tv, name: str) -> bool:
    """点「新建文件夹(&M)」→ 输入名字 → Enter 创建（用户给的方法）。"""
    mk = find_ctrl_any(bd, ["新建文件夹(&M)", "新建文件夹(M)", "新建文件夹"], cls="Button")
    if mk is None:
        raise RuntimeError("浏览文件夹里找不到「新建文件夹(&M)」按钮")
    focus_window(bd)
    click_ctrl(mk, f"浏览 -> 点「新建文件夹(&M)」（准备输入 {name!r}）", focus_win=bd)
    time.sleep(0.6)
    # 临时编辑框：可见+可用；隐藏那个只读禁用的要排除
    eds = [e for e in bd.descendants(class_name="Edit")
           if e.is_visible() and e.is_enabled()]
    import win32gui
    import win32con
    TVM_GETEDITCONTROL = 0x110F   # TV_FIRST(0x1100) + 15
    TVM_ENDEDITLABELNOW = 0x1116  # TV_FIRST(0x1100) + 22
    hedit = tv.send_message(TVM_GETEDITCONTROL, 0, 0)
    if not hedit and eds:
        hedit = eds[0].handle
    if not hedit:
        raise RuntimeError("点「新建文件夹」后树里没出现可编辑框")
    win32gui.SendMessage(hedit, win32con.WM_SETTEXT, 0, name)
    op_pause()
    # 【关键 2026-11-27 实测】只发 TVM_ENDEDITLABELNOW 会 ret=0 且目录名仍是
    # 「新建文件夹」；必须先给编辑框递 Enter 键消息，树才会用新文本结束编辑。
    win32gui.PostMessage(hedit, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0x001C0001)
    win32gui.PostMessage(hedit, win32con.WM_KEYUP, win32con.VK_RETURN, 0xC01C0001)
    time.sleep(0.9)
    ret = tv.send_message(TVM_ENDEDITLABELNOW, 1, 0)
    log(f"  浏览 -> 输入 {name!r} + Enter 消息结束编辑 (ret={ret})")
    op_pause()
    time.sleep(0.4)
    return True


def browse_select_folder(bd, drive: str, folder_name: str = SCAN_FOLDER_NAME) -> str:
    """【2026-11-27 用户定版 + 扩展】在主硬盘分区层定位，支持多级路径：

      单层 folder_name=`A_爱普生扫描_社媒`：
        浏览 → 找到「本地磁盘 (D:)」→ 单击选中 → 新建/选中该文件夹 → 确定。
      多层 folder_name=`A_爱普生扫描/jpg`（用 / 或 \\ 分隔）：
        浏览 → 选中盘符 → 展开找到「A_爱普生扫描」（已存在则选中）→ 在其下
        新建/选中「jpg」→ 确定。每一级都用真实鼠标点选，确保 shell 当前目录正确
        （否则「新建文件夹」会落到上一个旧目录里，实测踩过嵌套错位的坑）。

    已存在的层级直接选中，不重复新建。返回点确定前隐藏 Edit 读到的**末级**文件夹名，
    供调用方与「文件保存设置」路径框交叉校验（路径框只显示末级名）。
    """
    drive = drive.upper().rstrip(":")
    tv = next((x for x in bd.descendants(class_name="SysTreeView32")), None)
    if tv is None:
        raise RuntimeError("浏览文件夹里找不到 SysTreeView32")

    # 【2026-11-27 修复】SHBrowseForFolder 的"当前目录"只认真实鼠标点树节点，
    # 后台 TVM_SELECTITEM 只改 caret、不改 shell 当前目录 → 点"新建文件夹"会落在
    # 上次的旧目录里（实测建成了 D:\A_爱普生扫描\A_爱普生扫描_社媒 嵌套错误）。
    # 仅在"选盘符+新建+选中"这一步临时切真实鼠标，结束即恢复后台消息模式。
    global BACKGROUND
    _bg_before = BACKGROUND
    BACKGROUND = False
    try:
        return _browse_select_folder_real(bd, drive, folder_name, tv)
    finally:
        BACKGROUND = _bg_before


def _browse_select_folder_real(bd, drive: str, folder_name: str, tv) -> str:
    focus_window(bd)
    di = _tree_locate_drive(tv, drive + ":")
    if di is None:
        raise RuntimeError(f"浏览文件夹主分区层找不到「本地磁盘 ({drive}:)」")
    log(f"浏览 -> 主分区层定位 {_t(di)!r}")

    # 支持多级路径：'A_爱普生扫描/jpg' 或 'A_爱普生扫描\\jpg' → ['A_爱普生扫描','jpg']
    parts = [p.strip() for p in folder_name.replace("\\", "/").split("/") if p.strip()]
    if not parts:
        raise RuntimeError(f"文件夹名无效: {folder_name!r}")

    parent = di
    _tree_select_click(tv, parent, bd)   # 逐级操作前先选中当前父节点（真实鼠标模式）
    for part in parts:
        kids = _tree_children(parent)     # 展开父节点列出子项
        ch = _tree_match(kids, part)
        if ch is None:
            _tree_select_click(tv, parent, bd)   # 新建前确保父节点仍是选中态（shell 当前目录）
            _browse_new_folder(bd, tv, part)
            kids = _tree_children(parent)
            ch = _tree_match(kids, part)
            if ch is None:
                raise RuntimeError(f"在 {_t(parent)!r} 下新建 {part!r} 后在树里仍找不到该节点")
            log(f"浏览 -> 已在 {_t(parent)!r} 下新建文件夹 {part!r}")
        else:
            log(f"浏览 -> {_t(parent)!r} 下 {part!r} 已存在，直接选中（不重复新建）")
        # 选中本级节点；下一级会以此为父。末级即为最终目标。
        _tree_select_click(tv, ch, bd)
        parent = ch

    # 【2026-11-27 用户指出】第一次新建成功后没真正选中 → 路径框不对 → 得再开
    # 一次浏览重新选。这里先 TVM_SELECTITEM 选中目标节点；回显框（文件夹(F):）
    # 实测滞后不可靠（曾停在「新建文件夹」但路径框仍正确），只作日志参考。
    # 真正判据 = 点确定后「文件保存设置」路径框，由 apply_filesettings 校验，
    # 不对就自动重开浏览重新选中（= 把用户手动那一步内置）。
    shown = _browse_hidden_edit(bd)
    log(f"浏览 -> 已选中末级节点 {_t(parent)!r}，回显框={shown!r}（滞后仅供参考），点「确定」")

    okb = find_ctrl_any(bd, ["确定"], cls="Button")
    if okb is None:
        raise RuntimeError("浏览文件夹里找不到「确定」按钮")
    click_ctrl(okb, "浏览文件夹 -> 确定（路径写回文件保存设置）", focus_win=bd)
    time.sleep(0.8)
    return shown


def apply_filesettings(dlg, cfg: RunConfig):
    if cfg.probe:
        list_controls(dlg, BASE_DIR / "filesettings_dump.txt")
    focus_window(dlg)
    log(f"文件保存设置窗口: {dlg.window_text()!r}")

    # 位置：点"其它:"单选（实测按钮名是 '其它:'，且是 radio/checkbox）
    other = find_ctrl_any(dlg, ["其它:", "其它", "其它(&O)", "Other"], cls="Button")
    if other:
        try:
            checked = other.is_checked() if hasattr(other, "is_checked") else None
            if checked is not True:
                click_ctrl(other, focus_win=dlg)
                log("位置 -> 其它:")
        except Exception as e:
            log(f"WARN 其它: 按钮状态读取失败 {e}")

    # 路径：点「浏览(&B)...」→ 在主分区层新建「A_爱普生扫描」→ 确定。
    # 【2026-11-27 用户定版】执行前问磁盘（--drive），输出目录 = X:\A_爱普生扫描。
    # 不预建目录：让「浏览文件夹」对话框里的「新建文件夹」真正创建它。
    # 旧方案（隐藏 Edit 填路径 / BFFM_SETSELECTION / 树 type_keys）全部作废。
    drive = (getattr(cfg, "drive", "") or "D:").upper().rstrip(":")
    _folder = cfg.out_folder()  # 方案可指定文件夹名（rank/post 靠此区分）
    # 支持多级路径：'A_爱普生扫描/jpg' → 完整落盘路径 D:\A_爱普生扫描\jpg
    cfg.output_dir = str(Path(f"{drive}:\\{_folder.replace('/', chr(92))}"))
    log(f"输出目录 = {cfg.output_dir}（磁盘由用户指定，文件夹名={_folder}）")
    browse = find_ctrl_any(dlg, ["浏览(&B)...", "浏览(B)...", "浏览...", "浏览"], cls="Button")
    if not browse:
        raise RuntimeError("文件保存设置窗口找不到「浏览(&B)...」按钮，无法设置路径")

    # 路径框只显示末级名（如 'jpg'），多级路径要取最后一段做校验
    want_leaf = _folder.replace("\\", "/").split("/")[-1].strip()
    actual_leaf = ""
    shown = ""
    # 【2026-11-27 用户观察】第一次新建后没选中 → 回读不是目标名 → 又开一次浏览
    # 重新选中才行。把这次"人工补救"内置为自动重试（最多 2 轮）。
    for rnd in range(1, 3):
        click_ctrl(browse, f"路径 -> 点「浏览」选择文件夹（第 {rnd} 轮）", focus_win=dlg)
        time.sleep(1.0)
        d = Desktop(backend="win32")
        folder_dlg = None
        for w in safe_windows():
            try:
                if ("浏览文件夹" in w.window_text() and w.handle != dlg.handle
                        and w.class_name() == "#32770"):
                    folder_dlg = d.window(handle=w.handle)
                    break
            except Exception:
                pass
        if folder_dlg is None:
            raise RuntimeError("点「浏览」后未出现「浏览文件夹」对话框")
        log(f"文件夹选择对话框: {folder_dlg.window_text()!r} handle={folder_dlg.handle}")
        shown = browse_select_folder(folder_dlg, drive, _folder)

        # 验证：点确定后路径框（第一个可见 Edit）应显示末级目录名
        edits = [x for x in dlg.descendants(class_name="Edit") if x.is_visible()]
        log(f"文件保存设置 Edits: {[(w.handle, w.window_text()) for w in edits]}")
        actual_leaf = edits[0].window_text().strip() if edits else ""
        if _norm(actual_leaf) == _norm(want_leaf):
            break
        log(f"WARN 第 {rnd} 轮路径框={actual_leaf!r} != {want_leaf!r}（浏览选中 {shown!r}）"
            + ("，重开浏览重新选中" if rnd < 2 else "，已到最大重试"))
        time.sleep(0.5)
    if _norm(actual_leaf) != _norm(want_leaf):
        raise RuntimeError(
            f"路径设置失败：浏览对话框选中 {shown!r}，文件保存设置路径框却显示 "
            f"{actual_leaf!r}（期望 {want_leaf!r}）。已重试 2 轮仍失败，避免扫描落到旧路径。")
    log(f"路径校验通过：路径框={actual_leaf!r}（= 目标末级 {want_leaf!r}）")

    # 前缀 / 开始号码：Edit 顺序固定 = [路径, 前缀, 开始号码]
    edits = [e for e in dlg.descendants(class_name="Edit") if e.is_visible()]
    if len(edits) >= 2:
        edits[1].set_edit_text(cfg.filename_prefix)
        op_pause()
        log(f"前缀 (Edit1) -> {cfg.filename_prefix}")
    if len(edits) >= 3:
        edits[2].set_edit_text(cfg.start_number)
        op_pause()
        log(f"开始号码 (Edit2) -> {cfg.start_number}")

    # 图像格式：ComboBox 里选。实测选项含 'TIFF (*.tif)'（单页，有空格）和
    # 'Multi-TIFF (*.tif)'，配置里写的 'TIFF(*.tif)' 缺空格 → 用 combo_select_by_handle
    # 的模糊匹配能命中，但优先用 config 里给的精确名，找不到再模糊。
    h, cur = find_image_format_combo(dlg)
    if h:
        want = cfg.image_format
        # 规范化：配置里 'TIFF(*.tif)' 缺空格，实际选项是 'TIFF (*.tif)'
        if "TIFF" in want and "Multi" not in want:
            want = "TIFF (*.tif)"
        elif "JPEG" in want and "全真" not in want:
            want = "JPEG (*.jpg)"
        ok, msg = combo_select_by_handle(h, want)
        log(f"图像格式 (当前 {cur!r}) want={want!r} -> {msg}")
    else:
        log("WARN 图像格式 ComboBox 未找到")

    # TIFF 子选项：【流程方案驱动】方案给了 tiff_options 就设置；否则仅 test_ui 时进子对话框读取
    topt = getattr(cfg, "tiff_options", None)
    if "TIFF" in str(cfg.image_format).upper() and (topt is not None or getattr(cfg, "test_ui", False)):
        apply_tiff_plugin_options(dlg, cfg)

    # 「扫描后打开图像文件夹」：【流程方案驱动】按 open_folder 目标态设置（None=不改，兼容旧行为）
    want_of = getattr(cfg, "open_folder", None)
    if want_of is not None:
        apply_open_folder_checkbox(dlg, want_of)
    elif getattr(cfg, "test_ui", False):
        test_open_folder_checkbox(dlg, cfg)

    # JPEG 插件设置：压缩等级 + 编码标准 + 嵌入ICC（2026-11-27 用户要求）
    if "JPEG" in str(cfg.image_format).upper() and "全真" not in str(cfg.image_format):
        apply_jpeg_plugin_options(dlg, cfg)

    # 覆盖同名文件（BS_CHECKBOX，用 is_checked）
    ov = find_ctrl_any(dlg, ["替换所有相同名称的文件", "替换所有相同名称的文件(&M)"], cls="Button")
    if ov and cfg.overwrite:
        try:
            if not (ov.is_checked() if hasattr(ov, "is_checked") else False):
                click_ctrl(ov, focus_win=dlg)
                log("覆盖同名文件 -> 开")
        except Exception as e:
            log(f"WARN 覆盖选项设置失败: {e}")

    ok = find_ctrl_any(dlg, ["确定"], cls="Button")
    if ok:
        click_ctrl(ok, "文件保存设置 -> 确定", focus_win=dlg)
    else:
        raise RuntimeError("文件保存设置窗口找不到确定按钮")


def find_progress_window():
    """找「进程」进度窗口（扫描仪预热/读图/扫描时弹出）。

    【2026-11-27 修复】旧版用 top_windows()（spec）且只匹配标题=='进程'，
    扫描阶段实测窗口明明在（"正在扫描 1/23"）却全程返回 None——日志误报
    "进程窗口=已结束/未出现"，完成判据失效。
    新版：用 wrapper（safe_windows），标题精确或含「进程」，并兜底检查
    子控件 Static 是否含「正在扫描/正在处理数据」文本。
    """
    for w in safe_windows():
        try:
            if w.class_name() != "#32770":
                continue
            if "进程" in w.window_text():
                return w
            # 兜底：空标题但内容是扫描进度
            for c in w.descendants(class_name="Static"):
                try:
                    t = c.window_text()
                    if "正在扫描" in t or "正在处理数据" in t:
                        return w
                except Exception:
                    pass
        except Exception:
            pass
    return None


def wait_preview_ready(pwin, timeout=180):
    """等预览图像加载完毕。

    真实时序（用户确认）：点预览 -> 预览窗口先出空白框架 -> 「进程」窗口
    显示"扫描仪正在预热" -> 进度走完 -> 缩略图加载出来。

    【2026-11-26 用户澄清】预热期间「进程」窗口长时间存在、甚至系统显示
    "无响应"都是【正常现象】，不是卡死。绝不因超时就报 WARN/异常。
    唯一可靠的"加载完成"信号 = 缩略图（纯数字按钮）出现。全程只耐心轮询，
    每 3 秒报一次状态，直到缩略图到位。timeout 只是安全上限（默认 180s），
    到达也只记录状态，不否定扫描仪。
    """
    t0 = time.time()
    end = t0 + timeout
    last_report = 0
    while time.time() < end:
        n = len(thumb_checked(pwin))
        if n:
            log(f"  缩略图开始出现: {n} 张（预热耗时 {time.time()-t0:.0f}s）")
            break
        if time.time() - last_report >= 3:
            pg = find_progress_window()
            pg_desc = "在（扫描仪预热/读图中）" if pg else "无"
            log(f"  预览加载中 {time.time()-t0:.0f}s —— 进程窗口={pg_desc}，等待缩略图渲染 ...")
            last_report = time.time()
        time.sleep(0.5)
    else:
        log(f"  预览加载已等 {time.time()-t0:.0f}s，缩略图仍未出现（继续流程，请人工核对预览窗口）")
    # 再多等 0.8s，确保所有缩略图都渲染到位
    time.sleep(0.8)
    log(f"  缩略图状态: {len(thumb_checked(pwin))} 张")


def thumb_num_button(pwin, number: int):
    """精确找第 number 张的【数字复选框按钮】（纯数字名 + 可见）。

    不要用 find_visible_ctrl(pwin, [str(k)]) —— 它会因 descendants 顺序命中
    外层容器或同名隐藏按钮，导致点击无效（实测：逐张取消 24 个仍 24/24）。
    """
    for b in pwin.descendants(class_name="Button"):
        try:
            if b.is_visible() and b.window_text().strip() == str(number):
                return b
        except Exception:
            pass
    return None


def thumb_image_button(pwin, number: int):
    """找第 number 张缩略图的【图像按钮】（帧中心），用于单击选当前帧。

    实测控件树：每张有 3 个无名/数字 Button：
      外层容器 (859,121,1134,409)、图像 (876,136,1116,376)、数字勾 (978,384,1016,403)。
    图像按钮在数字勾正上方、中心 x 相同、宽高约 240。取 bottom 最大者（离勾最近）。
    """
    nums = {}
    for b in pwin.descendants(class_name="Button"):
        try:
            t = b.window_text().strip()
            if t.isdigit() and b.is_visible():
                nums[int(t)] = b.rectangle()
        except Exception:
            pass
    if number not in nums:
        return None
    nr = nums[number]
    best = None
    for b in pwin.descendants(class_name="Button"):
        try:
            if not b.is_visible() or b.window_text().strip():
                continue
            r = b.rectangle()
            if (180 < r.width() < 300 and 180 < r.height() < 300
                    and abs((r.left + r.right) / 2 - (nr.left + nr.right) / 2) < 40
                    and r.bottom <= nr.top + 5):
                if best is None or r.bottom > best[0]:
                    best = (r.bottom, b)
        except Exception:
            pass
    return best[1] if best else None


def open_preview_and_select_all(win, *, keep_open=False, arrange=True, select_one=True, select_index=1):
    """点预览 -> 等进程窗口走完 -> 移动并排 -> 选择帧 -> 校验。

    【2026-11-26 用户澄清的 EPSON 两段式选择逻辑】
      点「全选」按钮：只让所有帧【变蓝框/预选态】，不代表勾选生效；
      此时点某一帧的勾：全部生效（全选）；再点一次勾：全部取消。
      单击某一帧中心：只让该帧【变蓝框/预选态】；
      此时点该帧的勾：只选中这一张；再点一次勾：取消该张。

    因此"只选一张"必须：【全选按钮 -> 点勾 -> 全部取消】清零，
    再【单击第 1 帧中心 -> 点第 1 帧勾】才得到唯一勾选。
    select_one=False 时保留旧行为（全选：全选按钮 -> 点勾生效）。

    keep_open=True 时不关闭预览窗口；arrange=True 时移到主窗口右侧并排。
    """
    focus_window(win)  # 关键：先把主窗口激活到前台
    prev = find_ctrl_any(win, ["预览(&P)", "预览(P)", "预览"], cls="Button")
    if prev is None:
        raise RuntimeError("找不到预览按钮")
    click_ctrl(prev, "点击预览", focus_win=win)
    log("等待预览窗口（扫描仪预热可能需要 10-20 秒）...")

    # 等预览窗口出现
    pwin = None
    end = time.time() + 60
    while time.time() < end:
        for w in top_windows():
            try:
                if w.window_text() == "预览" and w.class_name() == "#32770":
                    pwin = w
                    break
            except Exception:
                pass
        if pwin:
            break
        time.sleep(0.5)
    if pwin is None:
        raise RuntimeError("预览窗口未出现；请检查扫描仪是否已冷启动")
    log(f"预览窗口就绪 handle={pwin.handle}")

    # 等进程窗口走完、图像加载完毕
    wait_preview_ready(pwin)

    # 报告 Tab 状态（0=普通 1=缩略图）与勾选状态
    tab = tab_current(pwin)
    checked = thumb_checked(pwin)
    log(f"预览状态: Tab={'普通' if tab == 0 else '缩略图' if tab == 1 else '未知'}, "
        f"已勾选 {sum(checked.values())}/{len(checked)} 张: {checked}")

    # 移动窗口与主窗口并排，方便人工对照
    if arrange:
        move_window_beside(pwin, win)

    if cfg_probe_flag():
        list_controls(pwin, BASE_DIR / "preview_dump.txt")

    # ---- 选帧前定点清理遮挡 ----
    # 扫描完成后 EPSON 自动打开的输出文件夹（CabinetWClass）会盖在缩略图按钮上，
    # set_focus 抢不过来，导致所有 click_input 打到它身上（勾选清不掉的根因）。
    # 后台消息模式不依赖前台，也不关资源管理器（关了就检测不到自动打开的文件夹）
    if BACKGROUND:
        log("后台消息模式：不改前台、不关窗口，开始选帧")
    else:
        close_blocking_explorers()
        bring_to_front(pwin)
        time.sleep(0.3)
        log("预览窗口已强制置顶，开始选帧")

    focus_window(pwin)
    allbtn = find_visible_ctrl(pwin, ["全选(&A)", "全选(A)", "全选"], cls="Button")
    num1 = thumb_num_button(pwin, 1)
    if select_one:
        # 【2026-11-27 定版】差集修正法（取代旧"两段式"）：
        #   实测消息级 BM_CLICK 点数字勾按钮 = 逐张 toggle，不会触发用户描述的
        #   "全选预选态→一次性生效/取消"两段逻辑。旧代码据此误判清零成功，
        #   结果 on=[2..24]、扫描排了 23 张（67 分钟）。
        #   正解：目标态 = 只有第 1 张勾选；逐张对比现状，不一致就点该张数字勾
        #   toggle，多轮直到收敛。校验不过直接抛错，绝不带着错误选择去扫描。
        target = max(1, int(select_index))
        imgt = thumb_image_button(pwin, target)
        if imgt is None:
            raise RuntimeError(f"找不到第 {target} 帧缩略图图像按钮，无法单选")
        click_ctrl(imgt, f"预览 -> 单击第 {target} 帧（激活）", focus_win=pwin)
        time.sleep(0.4)

        for rnd in range(1, 4):
            cur = thumb_checked(pwin)
            if not cur:
                raise RuntimeError("读不到任何缩略图勾选状态，无法单选")
            wrong = [k for k, v in cur.items() if v != (k == target)]
            if not wrong:
                break
            log(f"单选修正 {rnd}/3：当前勾 {[k for k,v in cur.items() if v][:8]}，"
                f"需切换 {wrong[:8]}")
            for k in wrong:
                nb = thumb_num_button(pwin, k)
                if nb:
                    click_ctrl(nb, focus_win=pwin)
            time.sleep(0.5)

        checked_after = thumb_checked(pwin)
        on = [k for k, v in sorted(checked_after.items()) if v]
        ok = on == [target]
        log(f"单选校验: {'通过：仅第 %d 张勾选' % target if ok else '异常'} on={on} / {len(checked_after)}")
        if not ok:
            raise RuntimeError(
                f"单选失败：实际勾选 {on}（期望 [{target}]）。已停止，避免误扫多张耗时长。"
                f"请人工核对预览勾选后重试。")
    else:
        # 【正常流程】全选扫描。
        # 【2026-11-27 修复】预览加载后默认已是全勾（实测 24/24）。此时若再点「全选」
        # 按钮，它其实是 toggle，会把全勾 flip 成全不勾（实测 0/24），随后消息级
        # BM_CLICK 点数字勾又与 is_checked() 读值脱节，修正 3 轮仍 0/24 → 误判失败。
        # 正解：目标态就是「全部勾选」，默认已达成时【绝不点任何勾按钮】，直接放行。
        # 仅当确实存在未勾帧时，才按用户方法点「全选」+点勾，再差集修正兜底。
        cur_before = thumb_checked(pwin)
        off_before = [k for k, v in cur_before.items() if not v]
        if not off_before:
            log(f"预览默认已全部勾选 {sum(cur_before.values())}/{len(cur_before)}，跳过勾选操作")
        else:
            log(f"预览有 {len(off_before)} 张未勾，按用户方法点全选+点勾：{off_before[:8]}")
            if allbtn and num1:
                click_ctrl(allbtn, "预览 -> 全选按钮", focus_win=pwin)
                time.sleep(0.4)
                click_ctrl(num1, "预览 -> 点勾（全选生效）", focus_win=pwin)
                time.sleep(0.5)
        for rnd in range(1, 4):
            cur = thumb_checked(pwin)
            off = [k for k, v in cur.items() if not v]
            if not off:
                break
            log(f"全选修正 {rnd}/3：还有 {len(off)} 张未勾 {off[:8]}，逐张打勾")
            for k in off:
                nb = thumb_num_button(pwin, k)
                if nb:
                    click_ctrl(nb, focus_win=pwin)
            time.sleep(0.5)
        checked_after = thumb_checked(pwin)
        all_on = bool(checked_after) and all(checked_after.values())
        log(f"全选校验: {'全部勾选' if all_on else '存在未勾选'} "
            f"{sum(checked_after.values())}/{len(checked_after)}")
        if not all_on:
            raise RuntimeError(
                f"全选未生效（{sum(checked_after.values())}/{len(checked_after)}），"
                f"已停止避免漏扫。请人工核对预览勾选后重试。")

    # 是否关闭预览窗口：默认保留（便于连续批次与人工核对）
    if not keep_open and pwin.window_text() != "EPSON Scan":
        try:
            pwin.close()
            log("关闭预览窗口")
        except Exception:
            pass
    return pwin


_cfg_probe = False
def cfg_probe_flag():
    return _cfg_probe


def _opened_folder_paths():
    """枚举资源管理器当前打开的文件夹绝对路径（Shell.Application COM）。

    用途：EPSON 扫完会自动打开输出文件夹；拿到它的真实路径后立刻检查里面的
    新图，而不是死盯脚本指定的那个目录（上轮实测路径没设成功，图落到了别的目录）。
    """
    paths = []
    try:
        from win32com.client import Dispatch
        from urllib.parse import unquote, urlparse
        sh = Dispatch("Shell.Application")
        for w in sh.Windows():
            try:
                url = w.LocationURL or ""
                if not url.lower().startswith("file:"):
                    continue
                s = unquote(urlparse(url).path).lstrip("/")
                if len(s) > 2 and s[1] == ":":
                    p = Path(s)
                    if p.is_dir() and p not in paths:
                        paths.append(p)
            except Exception:
                pass
    except Exception:
        pass
    return paths


def wait_new_file(folder: Path, before: set, timeout: int,
                  exts=(".tif", ".tiff", ".jpg", ".jpeg")):
    """等扫描完成并落盘。

    【2026-11-26/27 用户拍板的完成判据】EPSON 没有「扫描完成」提示框：
      点扫描 -> 「进程」窗口出现(读图) -> 进程窗口消失 -> 约 10 秒后
      输出文件夹被自动打开（因为勾了「扫描后打开图像文件夹」）。
    所以：进程窗口结束 + 文件夹自动打开 = 扫描已完成；紧接着【立刻去那个
    打开的文件夹里检查有没有新图】，并把实际落盘路径报出来。不再长时间空转、
    也不再只盯着脚本指定的目录（路径若没设成功，图会落到软件旧路径）。
    预热期间「进程」窗口长时间存在/显示无响应都属正常，日志保持中性。
    """
    t0 = time.time()
    end = t0 + timeout
    known_handles = set()
    try:
        known_handles = {w.handle for w in safe_windows()
                         if w.is_visible()
                         and w.class_name() in ("CabinetWClass", "ExploreWClass")}
    except Exception:
        pass

    target = Path(folder).resolve()
    baseline = {target: set(before)}   # 每个被监视目录各自的「开始前」快照
    prog_seen = False
    prog_end_at = None
    folder_seen = False
    last_report = 0

    def new_in(p: Path):
        """返回该目录相对基线新增的图像；首次见到的目录只建立基线。"""
        key = p.resolve()
        cur = snapshot_files(key, exts)
        base = baseline.get(key)
        if base is None:
            baseline[key] = cur
            return []
        return sorted(cur - base)

    while time.time() < end:
        pg = find_progress_window()
        if pg:
            prog_seen = True
            prog_end_at = None
        elif prog_seen and prog_end_at is None:
            prog_end_at = time.time()
            log("「进程」窗口已结束；等输出文件夹自动打开（约 10 秒）...")

        # 新弹出的资源管理器窗口 = 扫描完成信号
        opened_now = []
        try:
            for w in safe_windows():
                if (w.is_visible()
                        and w.class_name() in ("CabinetWClass", "ExploreWClass")
                        and w.handle not in known_handles):
                    known_handles.add(w.handle)
                    opened_now.append(w.window_text())
        except Exception:
            pass
        if opened_now:
            folder_seen = True
            log(f"输出文件夹自动打开: {opened_now}")

        # 监视目录 = 指定目录 + 所有资源管理器打开的文件夹
        scan_dirs = [target]
        for p in _opened_folder_paths():
            if p.resolve() != target and p not in scan_dirs:
                scan_dirs.append(p)
        for p in scan_dirs:
            new_in(p)   # 首次建立基线，避免把旧图误报成本次扫描

        found = []
        for p in scan_dirs:
            found += new_in(p)
        # 自动打开的文件夹：即使图在窗口弹出前就落盘（进了基线），
        # 也按「本次点击扫描之后修改过」认出来并报告实际路径。
        for p in scan_dirs[1:]:
            try:
                found += [x for x in snapshot_files(p, exts)
                          if x.stat().st_mtime >= t0 - 5]
            except Exception:
                pass
        found = sorted(set(found))
        if found:
            # 等文件大小稳定（EPSON 写 48bit TIFF 可能持续数十秒）
            last, stable = {}, 0
            while stable < 3 and time.time() < end:
                time.sleep(0.5)
                now = {p: p.stat().st_size for p in found if p.exists()}
                stable = stable + 1 if now == last else 0
                last = now
            # 【2026-11-27 修复】取消扫描/写盘未完成会留下 0 字节残 file，
            # 绝不能当"扫描完成"。只认 size>0 的；全是 0 字节则继续等。
            found = [p for p in found if p.exists() and p.stat().st_size > 0]
            if not found:
                log("发现新文件但均为 0 字节（可能扫描被取消/仍在写入），继续等待")
                time.sleep(1)
                continue
            for p in found:
                log(f"完成: {p} ({p.stat().st_size if p.exists() else 0} bytes)")
            actual_dirs = {x.parent for x in found}
            if actual_dirs != {target}:
                log(f"注意：实际落盘目录 = {[str(x) for x in actual_dirs]}，"
                    f"与指定目录 {target} 不同（以实际路径为准）")
            return found

        # 进程结束且文件夹已打开、又等了 15 秒仍无图 -> 收尾，不再空转
        if prog_end_at and folder_seen and time.time() - prog_end_at > 15:
            log("WARN 进程结束且输出文件夹已打开，但其中未发现本次扫描的图像；停止等待")
            return []

        if time.time() - last_report >= 5:
            # 【2026-11-27 用户澄清】点扫描后「进程」窗口可能延迟出现，属正常。
            # 三态描述，别把"还没出现"误报成"已结束"。
            if pg:
                pg_desc = "在（扫描/读图中）"
            elif prog_seen:
                pg_desc = "已结束"
            else:
                pg_desc = "暂未出现（延迟中，正常）"
            log(f"  等待落盘 {time.time()-t0:.0f}s —— 进程窗口={pg_desc}，"
                f"监视目录 {len(scan_dirs)} 个")
            last_report = time.time()
        time.sleep(1)
    log(f"WARN 等待 {timeout}s 未见新图像文件")
    return []


def snapshot_files(folder: Path, exts=(".tif", ".tiff", ".jpg", ".jpeg")):
    try:
        return {p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in exts}
    except Exception:
        return set()


def _escndv_pids():
    """返回所有 escndv.exe 的 PID（不依赖 psutil，用 tasklist）。"""
    import re
    import subprocess as sp
    try:
        raw = sp.run(["tasklist", "/FI", "IMAGENAME eq escndv.exe", "/FO", "CSV", "/NH"],
                     capture_output=True, timeout=8).stdout
        out = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        return set(int(m) for m in re.findall(r'"escndv\.exe","(\d+)"', out, re.I))
    except Exception:
        return set()


def close_epson_gracefully(timeout=25) -> bool:
    """正常关闭 EPSON Scan，严格按用户 2026-11-27 拍板的三步铁律：
      ① 先确认并关闭所有子窗口（配置/文件保存设置/浏览文件夹/预览）——消息级点「取消」
      ② 确认子窗口清空后，点主窗口「关闭(&C)」（或错误框「确定」）
      ③ 最后轮询等 escndv.exe 自己退出（只看窗口消失不够）

    ⚠ 铁律：绝不用 taskkill /F、绝不 WM_CLOSE 强关子窗口！
      · 强杀/孤儿进程会让驱动状态不复位，下次启动弹「无法与扫描仪连接」。
      · WM_CLOSE 强关文件保存设置/浏览文件夹 会连带杀掉主窗口。
      子窗口是模态的，不先关掉会挡住主窗口关闭 —— 所以顺序不能反。
    """
    import win32gui
    import win32process
    d = Desktop(backend="win32")
    eps_pids = _escndv_pids()

    def _is_eps_window(h):
        """只认属于 escndv.exe 进程的窗口，绝不误碰其它程序的对话框。"""
        if not eps_pids or not win32gui.IsWindow(h):
            return False
        try:
            return win32process.GetWindowThreadProcessId(h)[1] in eps_pids
        except Exception:
            return False

    # ① 先关所有 EPSON 子对话框（属于 escndv 进程、标题非主窗口的 #32770），绝不 WM_CLOSE
    for w in safe_windows():
        try:
            if not _is_eps_window(w.handle):
                continue
            if w.class_name() != "#32770" or w.window_text().strip() == "EPSON Scan":
                continue
            t = w.window_text().strip()
            sp = d.window(handle=w.handle)
            cancel = find_ctrl_any(sp, ["取消", "Cancel", "关闭(&C)", "关闭"], cls="Button")
            if cancel:
                h = cancel.wrapper_object().handle if hasattr(cancel, "wrapper_object") else cancel.handle
                msg_click_handle(h)
                op_pause()
                log(f"已消息级点子窗口「{t}」的「取消」")
                time.sleep(0.4)
        except Exception:
            pass

    # 复查：确认子窗口确实清空（模态框残留会挡主窗口关闭）
    time.sleep(0.5)
    left_child = [w.window_text().strip() for w in safe_windows()
                  if _is_eps_window(w.handle) and w.class_name() == "#32770"
                  and w.window_text().strip() != "EPSON Scan"]
    if left_child:
        log(f"WARN 子窗口点取消后仍在 {left_child}；尝试继续关主窗口")

    # ② 点主窗口「关闭(&C)」或错误框「确定」
    #    【2026-11-27 兜底】保存配置后进程可能短暂不稳定，PID 精确匹配可能落空。
    #    两遍：第一遍按 PID 精确；一个都没匹配到时第二遍放宽为"标题 EPSON Scan + 有关闭按钮"。
    clicked = False
    for strict_pid in (True, False):
        if clicked:
            break
        if not strict_pid:
            log("WARN 按 PID 未匹配到主窗口，放宽为标题匹配再试一次")
        for w in safe_windows():
            try:
                if strict_pid and not _is_eps_window(w.handle):
                    continue
                if w.window_text() != "EPSON Scan" or w.class_name() != "#32770":
                    continue
                sp = d.window(handle=w.handle)
                if sp.child_window(title="关闭(&C)", class_name="Button").exists(timeout=0.2):
                    focus_window(sp)
                    cb = sp.child_window(title="关闭(&C)", class_name="Button")
                    if BACKGROUND:
                        msg_click_handle(cb.wrapper_object().handle)
                    else:
                        cb.click_input()
                    op_pause()
                    log("已点主窗口「关闭(&C)」按钮（正常退出）")
                    clicked = True
                    break
                txt = " ".join(c.window_text() for c in w.children())
                if any(k in txt for k in ("无法与扫描仪连接", "请确认扫描仪", "错误")):
                    ok = find_ctrl_any(sp, ["确定", "OK"], cls="Button")
                    if ok:
                        focus_window(sp)
                        click_ctrl(ok, focus_win=sp)
                        log("已点错误对话框「确定」（让 EPSON 自行清理退出）")
                        clicked = True
                        break
            except Exception:
                pass
    if not clicked:
        log("无 EPSON 主窗口需要关闭（或无可点击按钮）")

    # ③ 等进程真正退出，不只看窗口消失。幂等：若之前已点过关闭，这里只负责等到进程消失。
    t0 = time.time()
    end = t0 + timeout
    last_report = 0
    while time.time() < end:
        pids = _escndv_pids()
        if not pids:
            log(f"EPSON 进程已正常退出（耗时 {time.time()-t0:.0f}s）")
            return True
        if time.time() - last_report >= 5:
            log(f"  等待 EPSON 自行退出 ... pid={pids} 已等 {time.time()-t0:.0f}s")
            last_report = time.time()
        time.sleep(0.5)
    log("WARN 已点关闭/确定，但 escndv.exe 超时未退出（不强杀；请手动确认）")
    return False


def launch_and_enter_main(cfg: RunConfig):
    """自己启动 EPSON Scan（detached，不随脚本退出被 kill），
    自动点机型选择对话框的「确定」，然后等主窗口控件加载完毕。

    实测（2026-11-26）：启动后先弹「选择扫描仪」#32770（有 ListBox + 确定），
    标题也是 "EPSON Scan"；必须点确定，否则软件等超时自行退出（这正是此前
    "启动后进程消失"的真正原因）。点确定后主窗口控件逐步加载，约 7s 后
    children 稳定在 110 且扫描按钮出现。
    """
    # 若已有 EPSON 主窗口，走正常关闭（点右下角「关闭(&C)」），绝不 taskkill
    close_epson_gracefully()
    time.sleep(0.8)
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen([EXE], creationflags=flags)
    log(f"--launch：已启动 EPSON Scan pid={proc.pid}（detached）")

    d = Desktop(backend="win32")

    def _spec(w):
        return d.window(handle=w.handle)

    def _visible():
        return safe_windows()

    # 阶段1：机型选择 -> 直接点「确定」（用户机器通常只有一台，默认项即可）
    clicked = False
    end = time.time() + 40
    while time.time() < end and not clicked:
        for w in _visible():
            try:
                if w.class_name() != "#32770":
                    continue
                sp = _spec(w)
                lb = sp.child_window(class_name="ListBox")
                if not lb.exists(timeout=0.1):
                    continue
                ok = sp.child_window(title="确定", class_name="Button")
                if not ok.exists(timeout=0.1):
                    continue
                items = lb.wrapper_object().item_texts()
                log(f"机型选择对话框: {items}")
                time.sleep(1.0)  # 对话框刚弹出，先停 1 秒等它完全就绪（点太快会失效）
                # 默认直接用列表首项（用户机器通常只有一台可用，首项=默认可用那台）。
                # 只有显式 --scanner 且命中列表时才改选——实测主动选 V700 会触发
                # 该机异常导致软件退出，而直接确定（GT-X970）可正常进主窗口。
                if cfg.scanner and cfg.scanner in items:
                    lb.wrapper_object().select(cfg.scanner)
                    op_pause()
                    log(f"选择扫描仪: {cfg.scanner}")
                else:
                    log(f"直接用默认机型: {items[0] if items else '?'}")
                if not BACKGROUND:
                    sp.set_focus()
                    time.sleep(0.5)
                if BACKGROUND:
                    msg_click_handle(ok.wrapper_object().handle)
                else:
                    ok.click_input()
                op_pause()
                clicked = True
                log("已点「确定」")
                break
            except Exception:
                pass
        if not clicked:
            time.sleep(0.3)
    if not clicked:
        raise RuntimeError("未出现机型选择对话框；请确认扫描仪已连接并通电")

    # 阶段2：等主窗口控件加载完毕
    # 【2026-11-26 修正】旧判据 children>50 且连续3次完全相等，在冷启动下 60s
    # 都满足不了（children 数会因控件逐批加载而小幅波动），但窗口其实早已可用。
    # 新判据：只要「扫描按钮出现」即认为就绪（这是唯一真信号），children>30 辅助。
    log("等待主窗口控件加载完毕（以扫描按钮出现为准）...")
    end = time.time() + 60
    last_report = 0
    while time.time() < end:
        cand = None
        for w in _visible():
            try:
                if w.window_text() == "EPSON Scan" and w.class_name() == "#32770":
                    cand = w
                    break
            except Exception:
                pass
        # 扫描仪断连时 EPSON 弹错误对话框（标题也是 EPSON Scan，children=3，
        # 含「无法与扫描仪连接」），此时主窗口永远不会出来——立即明确报错。
        for w in _visible():
            try:
                if w.window_text() == "EPSON Scan" and w.class_name() == "#32770" and len(w.children()) < 10:
                    txt = " ".join(c.window_text() for c in w.children())
                    if "无法与扫描仪连接" in txt or "请确认扫描仪" in txt:
                        raise RuntimeError(
                            "扫描仪未连接/未就绪（EPSON 弹出「无法与扫描仪连接」）。\n"
                            "请物理断电重启扫描仪，等完全启动后再重新运行本脚本。")
            except RuntimeError:
                raise
            except Exception:
                pass
        if cand:
            n = len(cand.children())
            sp = _spec(cand)
            has_scan = sp.child_window(title="扫描(&S)", class_name="Button").exists(timeout=0.1)
            if time.time() - last_report >= 3:
                log(f"  主窗口加载中 children={n} 扫描按钮={'在' if has_scan else '未出现'}")
                last_report = time.time()
            # 【2026-11-27 修正】children>30 是错的：启动后默认停在「全自动模式」，
            # 该模式只有扫描/自定义按钮，children=16，永远达不到 30 → 60s 超时，
            # 但软件其实早已就绪（扫描按钮在）。改为 children>10 即可，
            # 错误对话框有上面的关键词检测兜底，不会误判。
            if has_scan and n > 10:
                log(f"主窗口加载完毕 handle={cand.handle} children={n}")
                return sp
        time.sleep(0.4)
    raise RuntimeError("主窗口控件加载超时（60s）")


def acquire_main_window(cfg: RunConfig):
    """默认 attach：连接已打开的正常 EPSON Scan 主窗口，绝不新开/关闭软件。

    --launch：自己启动（detached）→ 自动点机型选择确定 → 等主窗口控件加载完。
    """
    if cfg.launch:
        return launch_and_enter_main(cfg)

    log("attach 模式：等待已打开的 EPSON Scan 主窗口 ...")
    main = wait_main(None)
    if main is None:
        raise RuntimeError(
            "未找到 EPSON Scan 主窗口。请先手动启动：\n"
            "  ① 扫描仪物理断电重启（关机再开）\n"
            "  ② 打开 EPSON Scan，机型选择点确定，进入专业模式\n"
            "  ③ 再运行本脚本；或直接用 --launch 让脚本自动完成上述步骤"
        )
    log(f"主窗口就绪 handle={main.handle}")
    return main


# 【2026-11-27 用户要求】磁盘容量预估：4800dpi 单张 JPG≈20MB / TIFF≈188MB。
# 只在 skill 安装完成的初始化阶段用一次：展示各盘可存张数 → 用户选盘 → 存入 config.json。
# 之后每次扫描流程直接读这个配置，不再询问。（纯标准库，无第三方依赖）
DISK_EST_MB = {"jpg": 20, "tiff": 188}


def disk_capacity_rows() -> list[dict]:
    """枚举本机固定磁盘，返回剩余空间 + 4800dpi 可存张数。

    Windows 纯标准库：ctypes.GetLogicalDrives 枚举盘符，GetDriveTypeW 只留固定盘，
    shutil.disk_usage 读剩余空间。读不到的盘静默跳过。
    """
    import ctypes
    import shutil
    import string

    rows: list[dict] = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        letters = [c for i, c in enumerate(string.ascii_uppercase) if (mask >> i) & 1]
    except Exception:
        letters = list(string.ascii_uppercase)
    for ch in letters:
        root = f"{ch}:\\"
        try:
            try:
                # 3 = DRIVE_FIXED；排除光驱/网络/内存盘。取不到类型时仍列出。
                if ctypes.windll.kernel32.GetDriveTypeW(root) not in (3,):
                    continue
            except Exception:
                pass
            u = shutil.disk_usage(root)
            free = u.free
            rows.append({
                "盘符": ch,
                "剩余GB": free / 1024 ** 3,
                "总GB": u.total / 1024 ** 3,
                "jpg": int(free // (DISK_EST_MB["jpg"] * 1024 ** 2)),
                "tiff": int(free // (DISK_EST_MB["tiff"] * 1024 ** 2)),
            })
        except Exception:
            continue
    rows.sort(key=lambda r: r["盘符"])
    return rows


def load_storage_drive() -> str:
    """读初始化时存下的「扫描图像存储磁盘」（config.json 的 `存储磁盘`），无则空串。"""
    try:
        d = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return str(d.get("存储磁盘", "") or "").strip().upper().rstrip(":")
    except Exception:
        return ""


def prompt_drive(default: str = "") -> str:
    """【仅初始化阶段调用】展示各盘剩余空间与可存张数，问一次存储磁盘，返回盘符字母。"""
    rows = disk_capacity_rows()
    default = (default or "").strip().upper().rstrip(":")
    print("\n=== 扫描图像存储磁盘（安装初始化，仅问这一次）===")
    print(f"4800dpi 单张估算：JPEG≈{DISK_EST_MB['jpg']}MB / TIFF≈{DISK_EST_MB['tiff']}MB"
          "（实际随画面内容浮动）")
    if rows:
        print(f"  {'盘符':<4}{'剩余':>10}{'总容量':>11}{'可存 JPG':>11}{'可存 TIFF':>12}")
        for r in rows:
            mark = "  ← 当前设置" if r["盘符"] == default else ""
            print(f"  {r['盘符'] + ':':<4}{r['剩余GB']:>9.1f}G{r['总GB']:>10.1f}G"
                  f"{r['jpg']:>10}张{r['tiff']:>11}张{mark}")
    else:
        print("  (未读到固定磁盘，可手动输入盘符)")
    hint = f"，直接回车沿用 {default}:" if default else ""
    try:
        ans = input(f"请选择存储磁盘（输入盘符如 D{hint}）: ").strip().upper().rstrip(":")
    except EOFError:
        ans = ""
    if not ans and default:
        ans = default
    if not ans:
        raise SystemExit("未选择磁盘，初始化取消（可稍后重新运行 --init）")
    if rows and ans not in [r["盘符"] for r in rows]:
        print(f"  WARN {ans}: 不在固定磁盘列表内，仍按你的输入保存。")
    return ans


def init_storage() -> str:
    """skill 安装完成的初始化：问一次存储磁盘，写入 config.json，之后流程直接读。

    可重复运行以改盘（例如换了容量更大的盘）；不会碰扫描流程本身。
    """
    drive = prompt_drive(load_storage_drive())
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}
    cfg["存储磁盘"] = drive
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n已保存存储磁盘 {drive}: -> {CONFIG_PATH.name}")
    print(f"之后扫描输出目录 = {drive}:\\A_爱普生扫描\\{{test|jpg|tiff}}\\")
    print("（以后 scan 流程不再询问；想换盘重新运行 --init）")
    return drive


def flow_fingerprint(flow_def: dict, drive: str = "", folder: str = "") -> str:
    """流程配置指纹：flows.json 里该方案的规范化内容 + 磁盘/文件夹覆盖。
    任何配置变化都会改指纹 → 快速模式自动失效，退回完整流程。"""
    import hashlib
    payload = json.dumps({
        "方案": flow_def or {},
        "磁盘": (drive or "").upper().rstrip(":"),
        "文件夹": folder or "",
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_state() -> dict:
    """读流程状态快照；不存在/损坏返回空 dict。"""
    try:
        d = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_state(flow_name: str, fingerprint: str, output_dir: str) -> None:
    """完整流程成功后写状态：记录该方案指纹、输出目录、时间戳。"""
    st = load_state()
    st.setdefault("流程", {})[flow_name] = {
        "指纹": fingerprint,
        "输出目录": str(output_dir).replace("/", chr(92)),
        "时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        "时间戳": int(time.time()),
    }
    st["最后更新"] = time.strftime("%Y-%m-%d %H:%M:%S")
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"流程状态已记录: {STATE_PATH.name} [{flow_name}] 输出={output_dir}")


def can_fast(flow_name: str, fingerprint: str) -> bool:
    """是否存在匹配的完整流程快照（流程需求未变）。"""
    rec = load_state().get("流程", {}).get(flow_name)
    if not rec:
        return False
    return rec.get("指纹") == fingerprint


def touch_state(flow_name: str) -> None:
    """快速模式跑过后只更新"最近使用时间"，不改指纹/输出目录。"""
    st = load_state()
    rec = st.get("流程", {}).get(flow_name)
    if rec:
        rec["快速最近使用"] = time.strftime("%Y-%m-%d %H:%M:%S")
        st["最后更新"] = time.strftime("%Y-%m-%d %H:%M:%S")
        STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_fast(cfg: RunConfig):
    """【效率优化】快速复用模式：跳过所有配置步骤，只做 启动→预览→全选→扫描。

    前提：上一次同方案已完整配置过（EPSON 会持久化文件保存设置/格式/USM/ICE），
    且流程需求未变（指纹匹配由 run_flow.py 判定）。输出目录从状态读取，供落盘监视用。
    """
    global _cfg_probe
    _cfg_probe = cfg.probe
    LOG_PATH.write_text("", encoding="utf-8")
    log("=== 快速模式：沿用上次配置，只做 预览→全选→扫描 ===")

    # 输出目录：优先用状态里记录的（上次完整流程实际用的），否则用方案算的
    st = load_state().get("流程", {}).get(cfg.flow_name, {})
    out_dir = Path(st.get("输出目录") or cfg.output_dir)
    cfg.output_dir = str(out_dir)
    log(f"沿用输出目录 = {cfg.output_dir}（上次 {st.get('时间', '未知')} 完整配置）")

    main = acquire_main_window(cfg)
    # 快速模式不切模式/不设参数：信任上次完整配置已就位。但主窗口必须真实存在。
    if main is None:
        raise RuntimeError("快速模式未找到主窗口")

    for idx in range(1, cfg.batch + 1):
        if idx > 1:
            if cfg.prompt:
                try:
                    ans = input(f"\n第 {idx-1} 张完成，放好下一张按 Enter（q 退出）:").strip().lower()
                    if ans == "q":
                        break
                except EOFError:
                    pass
            else:
                time.sleep(cfg.interval)

        log(f"=== 第 {idx}/{cfg.batch} 张（快速）===")
        # 预览 → 等缩略图 → 全选（默认全勾直接放行，不点全选按钮避免 flip）
        open_preview_and_select_all(main, keep_open=True, arrange=(idx == 1),
                                    select_one=cfg.select_one,
                                    select_index=getattr(cfg, "select_index", 1))
        # 文件保存设置 / USM / ICE 全部跳过（沿用 EPSON 持久化的上次设置）

        if cfg.dry_run:
            log("DRY RUN：快速模式到此为止，未点击扫描")
            continue

        before = snapshot_files(out_dir)
        main = refresh_main_if_stale(main)
        focus_window(main)
        scan = find_ctrl_any(main, ["扫描(&S)", "扫描(S)", "扫描"], cls="Button")
        if scan is None:
            raise RuntimeError("找不到扫描按钮（快速模式）")
        click_ctrl(scan, "快速模式 -> 点击扫描，等待落盘 ...", focus_win=main)
        new = wait_new_file(out_dir, before, cfg.wait_seconds)
        if new:
            log(f"本张完成，共 {len(new)} 个新图像文件")
        else:
            log(f"WARN 等待 {cfg.wait_seconds}s 未见新文件；请检查扫描仪状态")
            break

    # 快速模式只刷新"最后使用时间"，指纹保持完整模式记录的不变
    touch_state(cfg.flow_name)
    log("快速模式完成；由脚本正常关闭 EPSON（点关闭按钮）")


def run(cfg: RunConfig):
    global _cfg_probe
    _cfg_probe = cfg.probe
    LOG_PATH.write_text("", encoding="utf-8")
    log(f"profile={cfg.profile} scanner={cfg.scanner} dry_run={cfg.dry_run} "
        f"batch={cfg.batch} attach={not cfg.launch}")

    main = acquire_main_window(cfg)

    # 【流程第一步】窗口就位后立即切到专业模式——四种模式控件完全不同，
    # 不先切模式，后面的参数 ComboBox/配置按钮都不存在。切模式会重建窗口，
    # 故用返回的新 main（handle 可能变）。
    main = ensure_professional_mode(main, cfg)

    if cfg.probe:
        list_controls(main, BASE_DIR / "main_dump.txt")
        log("控件树 -> main_dump.txt（文件保存设置/预览窗口请用 --probe-interactive）")
        return

    # 主设置只在整批开始前配置一次；【文件保存设置必须在预览/选帧之后】——
    # 用户 2026-11-26 明确要求的完整流程：预览 -> 只选 1 张 -> 存储文件夹/格式 -> 扫描。
    # 若提前设置，点扫描时 EPSON 会用默认值覆盖，落回软件里旧的保存路径。
    apply_main_settings(main, cfg)

    # 主窗口「配置」对话框：rank 用无色彩校正 + 关自动曝光
    if not getattr(cfg, "skip_config", False):
        apply_config_dialog(main, cfg)

    out = Path(cfg.output_dir)
    # 目录由「浏览文件夹」对话框里的「新建文件夹(&M)」创建，脚本不预建
    if not out.is_dir():
        log(f"输出目录 {out} 尚未创建（由稍后的浏览对话框新建）")

    for idx in range(1, cfg.batch + 1):
        if idx > 1:
            if cfg.prompt:
                try:
                    ans = input(f"\n第 {idx-1} 张已完成，请放好下一张，按 Enter 继续（输入 q 提前退出）：").strip().lower()
                    if ans == "q":
                        log("用户提前退出")
                        break
                except EOFError:
                    pass
            else:
                log(f"--no-prompt：等待 {cfg.interval}s 后继续")
                time.sleep(cfg.interval)

        log(f"=== 第 {idx}/{cfg.batch} 张 ===")
        # 保留预览窗口并排，方便人工核对；下一张会复用/重新预览
        open_preview_and_select_all(main, keep_open=True, arrange=(idx == 1),
                                    select_one=cfg.select_one,
                                    select_index=getattr(cfg, "select_index", 1))

        # 关键顺序：选好 1 张之后，再走「存储文件夹 / 格式」流程
        if not cfg.use_saved_filesettings:
            dlg = open_filesettings(main)
            apply_filesettings(dlg, cfg)
        else:
            log("跳过文件保存设置（沿用软件内已保存设置）")

        # 文件保存设置确定后 EPSON 可能重建主窗口（旧句柄失效 → 控件全找不到），
        # 先刷新一次句柄，USM/ICE 和扫描都用刷新后的窗口。
        main = refresh_main_if_stale(main)

        # 【2026-11-27 用户拍板的新顺序】USM/ICE 在文件保存之后、点扫描之前才设，
        # 不在主设置阶段设——避免提前设被 EPSON 重置。
        apply_main_usm_ice(main, cfg)

        before = snapshot_files(out)

        if cfg.dry_run:
            log("DRY RUN：到此为止，未点击扫描")
            continue

        # 点扫描前先把主窗口激活到前台（否则点击落到别的窗口）
        focus_window(main)
        scan = find_ctrl_any(main, ["扫描(&S)", "扫描(S)", "扫描"], cls="Button")
        if scan is None:
            raise RuntimeError("找不到扫描按钮")
        click_ctrl(scan, "点击扫描，等待新文件落盘 ...", focus_win=main)

        new = wait_new_file(out, before, cfg.wait_seconds)
        if new:
            log(f"本张完成，共 {len(new)} 个新图像文件（路径见上）")
        else:
            log(f"WARN 等待 {cfg.wait_seconds}s 未见新文件；请检查扫描仪状态")
            break

    if cfg.launch:
        log("全部完成；由脚本正常关闭 EPSON（点关闭按钮）")
    else:
        log("全部完成；EPSON Scan 保持打开（attach 模式，由用户手动关闭）")

    # 【效率优化】完整流程成功后落状态快照，供下次走快速模式
    # dry-run 在扫描前 continue，`new` 从未赋值 → 用 locals().get 防 UnboundLocalError。
    if locals().get("new"):
        save_state(cfg.flow_name, getattr(cfg, "_fingerprint", ""), str(cfg.output_dir))


def _install_exit_hook(should_close: bool):
    """注册退出钩子：脚本结束/异常/被 Ctrl+C / SIGTERM 时，点主窗口关闭按钮正常退出。

    【2026-11-26 铁律】绝不 taskkill /F——强杀会让下次启动报错。
    只有本脚本 --launch 自己启动的软件才关；attach 模式（用户手动开的）绝不关。
    注意：SIGKILL / 进程组被环境强杀无法捕获，那种情况只能靠"不要用后台任务跑"来避免。
    """
    if not should_close:
        return
    import atexit
    import signal

    def _close():
        try:
            close_epson_gracefully()
        except Exception as ex:
            log(f"WARN 退出钩子关闭 EPSON 失败: {ex}")

    atexit.register(_close)

    def _handler(signum, frame):
        log(f"收到信号 {signum}，正常关闭 EPSON 后退出")
        _close()
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except Exception:
            pass


def main():
    global BACKGROUND
    p = argparse.ArgumentParser(description="EPSON Scan 3.9 一键扫描（默认后台消息模式，不占鼠标）")
    p.add_argument("--profile", choices=list(PROFILES), default="rank")
    p.add_argument("--scanner", default=None, help="扫描仪名称（默认不指定=机型选择对话框直接确定用首项）")
    p.add_argument("--preset", default=None, help="使用已保存的「当前设置」预设名")
    p.add_argument("--use-saved-filesettings", action="store_true",
                   help="跳过文件保存设置（沿用软件内已保存设置）")
    p.add_argument("--output-dir", default=None)
    p.add_argument("--prefix", default=None)
    p.add_argument("--resolution", default=None)
    p.add_argument("--jpeg-quality", type=int, default=None)
    p.add_argument("--wait", type=int, default=None, help="等待新文件秒数")
    p.add_argument("--batch", type=int, default=1, help="连续扫描张数（默认 1）")
    p.add_argument("--no-prompt", action="store_true", help="批内不等待换片确认，按 --interval 秒间隔")
    p.add_argument("--interval", type=int, default=3, help="--no-prompt 时每张间隔秒数（默认 3）")
    p.add_argument("--launch", action="store_true",
                   help="自己启动软件（仅探测用；默认连接已打开的窗口）")
    p.add_argument("--dry-run", action="store_true", help="不真正扫描")
    p.add_argument("--probe", action="store_true", help="连接已打开窗口，dump 主窗口控件树")
    p.add_argument("--test-ui", action="store_true",
                   help="专项测试：配置对话框 + TIFF子选项 + 打开文件夹勾选取消/重勾")
    p.add_argument("--drive", default="", help="扫描文件存到哪个磁盘（如 D）；不传则执行前交互询问")
    p.add_argument("--foreground", action="store_true",
                   help="切回真实鼠标模式（会占用鼠标/焦点；默认后台消息模式，不影响其他任务）")
    p.add_argument("--select-one", action="store_true",
                   help="只选第1张（测试用）；默认全选扫描")
    p.add_argument("--with-config", action="store_true",
                   help="不跳过主窗口「配置」对话框（默认跳过）")
    args = p.parse_args()
    if args.with_config:
        args.skip_config = False
    args.prompt = not args.no_prompt
    BACKGROUND = not args.foreground
    cfg = load_config(args)
    # 执行前问用户：扫描文件存到哪个磁盘
    drive = (getattr(cfg, "drive", "") or "").strip().upper().rstrip(":")
    if not drive:
        try:
            drive = input("扫描文件存在哪个磁盘？输入盘符（如 D）: ").strip().upper().rstrip(":")
        except EOFError:
            drive = ""
    if not drive:
        raise SystemExit("未指定磁盘盘符，已退出（可用 --drive D）")
    cfg.drive = drive
    cfg.output_dir = f"{drive}:\\{cfg.out_folder().replace('/', chr(92))}"
    log(f"目标磁盘 = {drive}:  输出目录 = {cfg.output_dir}  后台消息模式 = {BACKGROUND}")
    _install_exit_hook(should_close=cfg.launch)  # 只有自己启动的软件才负责正常关闭
    try:
        run(cfg)
    finally:
        # run() 正常/异常返回时也会触发 atexit；这里显式再调一次保证（内部幂等：无窗口直接返回）
        if cfg.launch:
            close_epson_gracefully()


if __name__ == "__main__":
    main()
