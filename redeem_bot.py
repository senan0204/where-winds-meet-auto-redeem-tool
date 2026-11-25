# -*- coding: utf-8 -*-
#
# 燕云十六声 兑换码批量填写助手
# Copyright (C) 2026 yunyi-zhao
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
燕云十六声 - 兑换码批量自动填写工具

使用前提：游戏用窗口/无边框模式，停在【设置 → 其他】页面，能看到「账户信息 → 兑换码」这一行。

------------------------------------------------------------------------------
以下每一条都是在游戏里实测出来的，不是猜的，改代码前先看这里
------------------------------------------------------------------------------
输入文字：必须用 KEYEVENTF_UNICODE 直发字符。
    扫描码模拟按键会被中文输入法截走，字进的是输入法候选窗而不是游戏输入框；
    而且通过 WM_IME_CONTROL 跨进程关输入法对这个游戏无效（状态查询返回已关闭，
    实际照样弹候选窗）。Unicode 直发绕开键盘布局和输入法，实测干净可靠。

提交：必须用鼠标点底部的「Space 确认」按钮。
    Space 键按下去毫无反应（输入框聚焦时被吃掉）。点按钮实测能正常提交。

取消/退回：必须用鼠标点底部的「Esc 取消」按钮。
    Esc 键只在没点过输入框时有效；一旦输入框获得焦点，Esc 就被输入框吃掉了。

结果判断：不需要 OCR。
    兑换成功    → 游戏自己关掉弹窗，画面回到设置页，顶部飘「兑换码使用成功…」（5.png）
    已使用/无效 → 弹窗照旧开着，输入框里还留着上一个码（4.png），顶部飘报错
    所以「弹窗关没关」就等于「成功没成功」，顺手把每个码的结果报出来。

安全红线：确认/取消这两个按钮的屏幕坐标，在设置页上正好压着「挑战锁」的【开启】按钮。
    要是状态判断错了、在设置页上误点一下，就把玩家的挑战锁给开了。所以点这两个按钮前
    必须两道校验都通过（整体状态是弹窗层 + 按钮那一行的像素是弹窗底栏的深色），
    任何一道不过就中止整个流程，绝不硬着头皮点下去。

判断在哪一层靠锚点像素亮度：设置页背景是浅灰亮色（实测 ~201），弹窗一开整屏压暗（实测 ~56），
区分度极大。这也意味着游戏必须是窗口/无边框模式——独占全屏读不到像素，
本程序会直接拒绝运行而不是盲点。

只依赖 Python 标准库（ctypes + tkinter），不需要 pip 安装任何东西。
"""

import ctypes
import json
import os
import re
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from ctypes import wintypes
from datetime import date, datetime, timedelta
from tkinter import messagebox, scrolledtext, ttk

# --------------------------------------------------------------------------
# DPI 感知：不加这句在缩放不是 100% 的机器上坐标会算错
# --------------------------------------------------------------------------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# --------------------------------------------------------------------------
# SendInput 相关结构体定义
# --------------------------------------------------------------------------
ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

VK_ESC = 0x1B
VK_F11 = 0x7A
VK_F12 = 0x7B

# Set-1 扫描码
SC_ESC = 0x01
SC_BACKSPACE = 0x0E
SC_SPACE = 0x39
SC_LSHIFT = 0x2A
SC_LCTRL = 0x1D
SC_A = 0x1E
SC_V = 0x2F
SC_DELETE = 0x53  # 扩展键


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


# 这几个 API 返回的是 SHORT，不声明 restype 的话 ctypes 按 int 读，高位可能是脏数据
user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.VkKeyScanW.restype = wintypes.SHORT
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = wintypes.SHORT

# GDI：GetPixel 返回 COLORREF(DWORD)。不声明 restype 的话 ctypes 按 c_int 读，
# CLR_INVALID(0xFFFFFFFF) 会变成 -1，等值判断永远不成立，然后 -1 & 0xFF = 255 ——
# 读不到的像素会被当成纯白，状态判断整个失真。HDC 同理要按 64 位句柄声明。
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
gdi32.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.GetPixel.restype = wintypes.COLORREF

# 窗口操作：倒计时结束后把游戏窗口带回前台用
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL

# 句柄是 64 位的，不声明 argtypes 的话 ctypes 会按 c_int 传参然后溢出
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE


def _send(*inputs):
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT))


def _key_input(scan, keyup=False, extended=False):
    flags = KEYEVENTF_SCANCODE
    if keyup:
        flags |= KEYEVENTF_KEYUP
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    return INPUT(type=INPUT_KEYBOARD,
                 ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=None))


def key_tap(scan, hold=0.03, extended=False):
    _send(_key_input(scan, False, extended))
    time.sleep(hold)
    _send(_key_input(scan, True, extended))


def key_combo(mod_scan, scan, hold=0.03):
    _send(_key_input(mod_scan))
    time.sleep(hold)
    _send(_key_input(scan))
    time.sleep(hold)
    _send(_key_input(scan, True))
    time.sleep(hold)
    _send(_key_input(mod_scan, True))


def type_char_scan(ch, hold=0.02):
    """按键盘布局转扫描码敲。会被中文输入法截走，只作为备用方案保留。"""
    res = user32.VkKeyScanW(ch)
    if res == -1:
        return False
    vk = res & 0xFF
    need_shift = bool((res >> 8) & 0x01)
    scan = user32.MapVirtualKeyW(vk, 0)
    if scan == 0:
        return False
    if need_shift:
        _send(_key_input(SC_LSHIFT))
        time.sleep(hold)
    key_tap(scan, hold)
    if need_shift:
        time.sleep(hold)
        _send(_key_input(SC_LSHIFT, True))
    return True


def type_char_unicode(ch, hold=0.015):
    """直接发 Unicode 字符，绕过键盘布局和输入法。实测这条路才打得进游戏输入框。"""
    code = ord(ch)
    _send(INPUT(type=INPUT_KEYBOARD,
                ki=KEYBDINPUT(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE,
                              time=0, dwExtraInfo=None)))
    time.sleep(hold)
    _send(INPUT(type=INPUT_KEYBOARD,
                ki=KEYBDINPUT(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                              time=0, dwExtraInfo=None)))


def move_and_click(x, y, pre_delay=0.15, click_hold=0.06):
    """绝对坐标移动 + 左键单击。用虚拟桌面归一化坐标，多屏也正确。"""
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) or 1
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) or 1
    nx = int((x - vx) * 65535 / max(vw - 1, 1))
    ny = int((y - vy) * 65535 / max(vh - 1, 1))
    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    _send(INPUT(type=INPUT_MOUSE,
                mi=MOUSEINPUT(dx=nx, dy=ny, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=None)))
    time.sleep(pre_delay)  # 给游戏 UI 反应 hover 的时间，否则有些按钮点不动
    _send(INPUT(type=INPUT_MOUSE,
                mi=MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_LEFTDOWN,
                              time=0, dwExtraInfo=None)))
    time.sleep(click_hold)
    _send(INPUT(type=INPUT_MOUSE,
                mi=MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_LEFTUP,
                              time=0, dwExtraInfo=None)))


def read_pixel(x, y):
    """读屏幕某点颜色，返回 (r, g, b)；独占全屏读不到，返回 None"""
    hdc = user32.GetDC(0)
    if not hdc:
        return None
    try:
        val = gdi32.GetPixel(hdc, int(x), int(y))
        if val == 0xFFFFFFFF:  # CLR_INVALID
            return None
        return (val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF)
    finally:
        user32.ReleaseDC(0, hdc)


def pixel_brightness(x, y):
    rgb = read_pixel(x, y)
    if rgb is None:
        return None
    r, g, b = rgb
    return int(0.299 * r + 0.587 * g + 0.114 * b)


def set_clipboard(text):
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    buf = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buf)  # 已含结尾的 \0
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not h:
            return False
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            kernel32.GlobalFree(h)
            return False
        ctypes.memmove(ptr, buf, size)
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)  # 交接失败才由我们释放，成功后内存归剪贴板所有
            return False
    finally:
        user32.CloseClipboard()
    return True


def get_cursor_pos():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def screen_size():
    return user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)


def key_pressed(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def ui_scale():
    """DPI 缩放系数。tk 的字体按点数自动跟随 DPI 放大，但我们写死的窗口尺寸、
    换行宽度这些像素值不会，200% 缩放下内容会溢出窗口，所以都要乘上这个系数。"""
    try:
        return max(1.0, user32.GetDpiForSystem() / 96.0)
    except Exception:
        return 1.0


def is_admin():
    """游戏通常以管理员权限运行；我们权限更低时 SendInput 会被 UIPI 静默丢弃
    （SendInput 照样返回成功，游戏却什么都收不到），所以只能靠事前提醒。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return True   # 查不到就当有，避免误报骚扰


SW_RESTORE = 9


def bring_game_foreground(title, settle=0.6):
    """把游戏窗口带回前台。返回 True/False；找不到窗口返回 None。
    点开始时本程序自己在前台，前台进程有权转让前台，SetForegroundWindow 能成功。"""
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return None
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.3)
    user32.SetForegroundWindow(hwnd)
    time.sleep(settle)
    return user32.GetForegroundWindow() == hwnd


# --------------------------------------------------------------------------
# 从兑换码网站抓取
# 该站是纯静态页，所有码内嵌在 <script> 里的  const DATA = [...]  数组中，
# 按日期分组：{ date: "8/27", codes: [...] }，另有一组 { date: "無時限", ... }。
# 个别条目带「｜备注」后缀，可兑换的值是「｜」之前的部分（页面 JS 里也是这么取的）。
# --------------------------------------------------------------------------
FETCH_TIMEOUT = 15


def parse_site_html(html):
    """从页面 HTML 里解出 [(日期标签, [码...]), ...]。解析失败抛 ValueError。"""
    m = re.search(r"const\s+DATA\s*=\s*\[(.*?)\]\s*;", html, re.S)
    if not m:
        raise ValueError("The page has no DATA array. The website layout may have changed.")
    body = re.sub(r"//[^\n]*", "", m.group(1))   # 数组里夹着 // 注释行
    groups = []
    for gm in re.finditer(r'\{[^{}]*?date\s*:\s*"([^"]*)"[^{}]*?codes\s*:\s*\[([^\]]*)\]', body):
        label = gm.group(1).strip()
        codes = [c.split("｜")[0].strip() for c in re.findall(r'"([^"]*)"', gm.group(2))]
        codes = [c for c in codes if c]
        if codes:
            groups.append((label, codes))
    if not groups:
        raise ValueError("The DATA array contained no readable groups. The website layout may have changed.")
    return groups


def fetch_site_codes(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=FETCH_TIMEOUT).read().decode("utf-8", "replace")
    return parse_site_html(html)


def parse_site_date(label, today=None):
    """网站的日期是不带年份的 M/D。按今年补全；补出来比今天晚 7 天以上就当成去年的
    （跨年后旧条目会出现这种情况）。解析不了（如「無時限」）返回 None。"""
    today = today or date.today()
    m = re.fullmatch(r"(\d{1,2})\s*/\s*(\d{1,2})", label)
    if not m:
        return None
    mo, d = int(m.group(1)), int(m.group(2))
    for year in (today.year, today.year - 1):
        try:
            dt = date(year, mo, d)
        except ValueError:      # 比如去年没有 2/29
            continue
        if dt <= today + timedelta(days=7):
            return dt
    return None


def parse_user_date(s, today=None):
    """用户输入的起始日期，接受 2026-08-27 / 2026/8/27 / 2026.8.27 / 8/27（当年）。"""
    today = today or date.today()
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})", s)
    if m:
        try:
            return date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
# 打包成 exe 后 __file__ 指向 PyInstaller 的临时解压目录，配置写进去下次启动就没了，
# 所以冻结状态下要用 exe 自己所在的目录。
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    # 坐标以「占屏幕宽/高的比例」保存，换分辨率不用重新校准（同为 16:9 时）
    # 默认值量自 2559x1439 截图，并在 2560x1440 实机验证过
    "point_entry": [0.5400, 0.3050],    # 设置页「兑换码」行右侧的 >> 按钮
    "point_input": [0.5045, 0.5076],    # 弹窗里的输入框
    "point_ok": [0.5355, 0.6551],       # 弹窗底部「Space 确认」按钮
    "point_cancel": [0.4615, 0.6551],   # 弹窗底部「Esc 取消」按钮

    # 主锚点：设置页背景的浅色区域。设置页实测亮度 ~201，弹窗层 ~56
    "anchor_point": [0.3000, 0.2427],
    "anchor_threshold": 140,
    # 副锚点：和确认/取消按钮同一行、但左边一截的位置。
    # 弹窗层这里是底栏的深色，设置页这里是「挑战锁」那一行的浅色。
    # 点确认/取消前用它做第二道校验，防止在设置页误点开挑战锁。
    "guard_point": [0.2500, 0.6551],
    "guard_threshold": 120,             # 亮度 < 该值才认为确实在弹窗底栏上

    "input_method": "unicode",          # unicode(实测可用) / scancode(被输入法吃) / clipboard
    "submit_method": "click",           # click(实测可用) / space(实测无效，仅备用)
    "clear_method": "backspace",        # backspace(实测可用) / ctrl_a / none
    "backspace_times": 30,              # 失败时输入框留着上一个码，次数要大于码长

    "continuous_mode": False,           # 失败时弹窗没关，直接重输下一个，省一次开关

    "game_window_title": "燕云十六声",   # 倒计时结束后按这个标题找游戏窗口并带回前台

    # 兑换码网站抓取
    "fetch_url": "https://raellen.github.io/wherewindsmeet-coupon/",
    "fetch_since": "",                   # 上次用的起始日期（YYYY-MM-DD），空 = 默认今天
    "fetch_include_noexpiry": False,     # 是否把「無時限」组也抓进来

    "start_countdown": 5,
    "delay_after_entry": 1.2,           # 点开 >> 后等弹窗
    "delay_after_click_input": 0.4,     # 点输入框后等聚焦
    "key_interval": 0.04,
    "delay_before_submit": 0.4,
    "delay_after_confirm": 3.0,         # 等结果上限；成功时弹窗一关就提前结束
    "delay_after_cancel": 1.0,
    "delay_between_codes": 0.6,
}


# 枚举型选项的合法取值。旧版本的配置文件里可能留着已经废弃的值
# （比如早期的 input_method="keyboard"），照单全收会静默退回到走不通的输入方式，
# 所以这里逐项校验，非法的一律回落到默认值。
_ENUMS = {
    "input_method": {"unicode", "scancode", "clipboard"},
    "submit_method": {"click", "space"},
    "clear_method": {"backspace", "ctrl_a", "none"},
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if not os.path.exists(CONFIG_PATH):
        return cfg
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except Exception:
        return cfg
    if not isinstance(saved, dict):
        return cfg

    for key, default in DEFAULT_CONFIG.items():
        if key not in saved:          # 新增的配置项：老文件里没有，用默认值
            continue
        val = saved[key]
        if key in _ENUMS:
            if val in _ENUMS[key]:
                cfg[key] = val
        elif isinstance(default, list):   # 坐标：必须是 [0,1] 区间内的两个数
            if (isinstance(val, list) and len(val) == 2
                    and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in val)):
                cfg[key] = [float(val[0]), float(val[1])]
        elif isinstance(default, bool):
            if isinstance(val, bool):
                cfg[key] = val
        elif isinstance(default, (int, float)):
            if isinstance(val, (int, float)) and not isinstance(val, bool) and val >= 0:
                cfg[key] = type(default)(val)
        elif isinstance(default, str):
            if isinstance(val, str) and val.strip():
                cfg[key] = val
    return cfg


def save_config(cfg):
    """exe 可能被放在写保护目录（比如 Program Files），失败不能炸，返回 False 让调用方提示。"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


class Aborted(Exception):
    """用户按了 F12 / 点了停止"""


class UnsafeState(Exception):
    """画面不是预期状态，继续操作有风险，中止"""


# --------------------------------------------------------------------------
# 主界面
# --------------------------------------------------------------------------
APP_TITLE = "Where Winds Meet Bulk Code Redeemer by Senan"
DISCORD_INVITE = "https://discord.gg/aCDGuHdqD"
CODES_WEBSITE = "https://codes.yar.gg/"


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.worker = None
        self.stop_flag = threading.Event()

        root.title(APP_TITLE)
        # 内容排下来大约 900 高（100% 缩放时）。给小了的话「开始兑换」按钮和运行日志
        # 会被挤出可视区，第一次打开的人会以为没有开始按钮。高 DPI 下字体自动放大，
        # 窗口和换行宽度也要按同系数放大；屏幕不够高时自动缩，但不低于能用的下限。
        self.k = k = ui_scale()
        sw, sh = screen_size()
        w = int(940 * k)
        h = min(int(740 * k), max(int(580 * k), sh - int(120 * k)))
        root.geometry(f"{w}x{h}")
        root.minsize(int(780 * k), int(580 * k))

        self._build_ui()
        self._refresh_labels()
        self._poll_hotkey()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.admin = is_admin()
        if not self.admin:
            self.log("Administrator privileges are missing. If the game runs as administrator, Windows may block simulated input. "
                     "The tool can appear to run while the game receives nothing. "
                     "Close the tool and select Run as administrator from the executable's right-click menu.")

    def _on_close(self):
        """运行中直接关窗口会把工作线程杀在按键按下与抬起之间，Shift/Ctrl 可能卡在
        系统级按下状态。先叫停线程等它收尾，再兜底把修饰键都抬起来。"""
        worker_was_alive = self.worker and self.worker.is_alive()
        if worker_was_alive:
            self.stop_flag.set()
            self.worker.join(timeout=2.0)
            for sc in (SC_LSHIFT, SC_LCTRL):
                _send(_key_input(sc, True))   # 对未按下的键发抬起是无害的
        self.root.destroy()

    # ---------------- UI ----------------
    def _build_ui(self):
        root = self.root
        paper, ink, muted = "#fafafa", "#171717", "#555555"
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", background=paper, foreground=ink, font=("Segoe UI", 10),
                        bordercolor="#bebebe", fieldbackground="white", selectbackground="#303030",
                        selectforeground="white", lightcolor=paper, darkcolor=paper,
                        focuscolor="#777777", troughcolor="#ededed")
        style.configure("TButton", padding=(12, 6), background="#ededed", relief="flat")
        style.map("TButton", background=[("pressed", "#cccccc"), ("active", "#dedede")],
                  foreground=[("disabled", "#777777")])
        style.configure("Ink.TButton", background=ink, foreground="white", font=("Segoe UI", 10, "bold"))
        style.map("Ink.TButton", background=[("disabled", "#dedede"), ("active", "#393939")],
                  foreground=[("disabled", "#555555")])
        style.configure("TNotebook.Tab", padding=(12, 6))
        style.map("TNotebook.Tab", background=[("selected", "white"), ("!selected", "#e8e8e8")])
        for control in ("TCheckbutton", "TRadiobutton"):
            style.map(control, background=[("active", "#ededed")],
                      indicatorbackground=[("selected", "#222222"), ("!selected", "white")],
                      indicatorforeground=[("selected", "white")])
        root.configure(bg=paper)
        root.iconbitmap(default=os.path.join(os.path.dirname(__file__), "icon.ico"))
        rail = tk.Frame(root, bg=ink, width=int(172 * self.k))
        rail.pack(side=tk.LEFT, fill=tk.Y)
        rail.pack_propagate(False)
        tk.Label(rail, text="SENAN", font=("Segoe UI", 19, "bold"), bg=ink, fg="white").pack(
            anchor="w", padx=18, pady=(22, 0))
        tk.Label(rail, text="Where Winds Meet", font=("Segoe UI", 10), bg=ink, fg="#c7c7c7").pack(
            anchor="w", padx=18, pady=(0, 28))
        tk.Button(rail, text="Tap to join our\nDiscord server ↗", command=lambda: webbrowser.open(DISCORD_INVITE),
                  bg="#292929", fg="white", activebackground="#454545", activeforeground="white",
                  relief="flat", cursor="hand2", anchor="w", justify="left", padx=12, pady=10,
                  font=("Segoe UI", 10)).pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=12)

        main = ttk.Frame(root, padding=(22, 18, 22, 12))
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        heading = ttk.Label(main, font=("Georgia", 26), text="")
        heading.pack(anchor="w")
        ttk.Separator(main).pack(fill=tk.X, pady=(10, 12))
        pages = ttk.Frame(main)
        pages.pack(fill=tk.BOTH, expand=True)
        pages.rowconfigure(0, weight=1)
        pages.columnconfigure(0, weight=1)
        views = {}
        links = {}

        def show(name):
            views[name].tkraise()
            heading.configure(text=name)
            for label, button in links.items():
                selected = label == name
                button.configure(background="white" if selected else ink,
                                 foreground=ink if selected else "white")

        for name in ("Code queue", "Game settings", "Activity log", "How to use"):
            page = ttk.Frame(pages)
            page.grid(row=0, column=0, sticky="nsew")
            views[name] = page
            button = tk.Button(rail, text=name, anchor="w", font=("Segoe UI", 11),
                               padx=12, pady=8, bd=0, relief="flat", cursor="hand2",
                               activebackground="#dedede", activeforeground=ink,
                               command=lambda name=name: show(name))
            button.pack(fill=tk.X, padx=10, pady=2)
            links[name] = button

        queue = views["Code queue"]
        ttk.Label(queue, text="Paste one code per line. Blank lines and # comments are skipped.",
                  foreground=muted).pack(anchor="w", pady=(0, 8))
        editor_colors = dict(background="white", foreground=ink, insertbackground=ink,
                             selectbackground="#303030", selectforeground="white", relief="flat",
                             highlightthickness=1, highlightbackground="#c9c9c9",
                             highlightcolor="#555555", padx=12, pady=10)
        self.txt_codes = scrolledtext.ScrolledText(queue, height=5, width=20, undo=True,
                                                  font=("Consolas", 12), **editor_colors)
        self.txt_codes.pack(fill=tk.BOTH, expand=True)
        tools = ttk.Frame(queue)
        tools.pack(fill=tk.X, pady=(8, 16))
        ttk.Button(tools, text="Open codes.yar.gg ↗",
                   command=lambda: webbrowser.open(CODES_WEBSITE)).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(tools, text="Paste", command=self.paste_codes).pack(side=tk.LEFT)
        ttk.Button(tools, text="Clear", command=self.clear_codes).pack(side=tk.LEFT, padx=6)
        self.lbl_count = ttk.Label(tools, font=("Consolas", 10))
        self.lbl_count.pack(side=tk.RIGHT)
        self.txt_codes.bind("<KeyRelease>", lambda event: self._update_count())
        ttk.Label(queue, text="Import published codes", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        fetch = ttk.Frame(queue)
        fetch.pack(fill=tk.X, pady=5)
        ttk.Label(fetch, text="Posted on or after").pack(side=tk.LEFT)
        self.var_since = tk.StringVar(value=self.cfg["fetch_since"] or date.today().isoformat())
        ttk.Entry(fetch, textvariable=self.var_since, width=12).pack(side=tk.LEFT, padx=8)
        self.btn_fetch = ttk.Button(fetch, text="Fetch codes", command=self.fetch_codes)
        self.btn_fetch.pack(side=tk.LEFT)
        self.var_noexpiry = tk.BooleanVar(value=self.cfg["fetch_include_noexpiry"])
        ttk.Checkbutton(queue, text="Include codes with no listed expiry",
                        variable=self.var_noexpiry).pack(anchor="w")

        settings = ttk.Notebook(views["Game settings"])
        settings.pack(fill=tk.X, anchor="n")
        pf = ttk.Frame(settings, padding=10)
        settings.add(pf, text="Calibration")
        ttk.Label(pf, text="Hover over a target in-game and press F11.",
                  foreground=muted).grid(row=0, column=0, columnspan=2, sticky=tk.W)
        self.pt_labels = {}
        pts = [
            ("point_entry", "Redeem Code >>", "the >> button beside Redeem Code in Settings"),
            ("point_input", "Dialog input", "the input box in the redeem dialog"),
            ("point_ok", "Confirm button", "Space Confirm at the bottom of the redeem dialog"),
            ("point_cancel", "Cancel button", "Esc Cancel at the bottom of the redeem dialog"),
            ("anchor_point", "Main anchor", "a blank light area on Settings, away from text"),
            ("guard_point", "Secondary anchor", "a blank area just left of Confirm in the dialog footer"),
        ]
        for i, (key, name, what) in enumerate(pts, start=1):
            lbl = ttk.Label(pf, text="", font=("Consolas", 9))
            lbl.grid(row=i, column=0, sticky=tk.W, pady=1)
            self.pt_labels[key] = (lbl, name)
            ttk.Button(pf, text="Calibrate", width=9,
                       command=lambda k=key, w=what: self.calibrate(k, w)
                       ).grid(row=i, column=1, padx=4, pady=1)
        ttk.Button(pf, text="Reset positions", command=self.reset_points
                   ).grid(row=len(pts) + 1, column=0, sticky=tk.W, pady=(6, 0))
        ttk.Button(pf, text="Check screen state", command=self.test_state
                   ).grid(row=len(pts) + 1, column=1, pady=(6, 0))

        mf = ttk.Frame(settings, padding=10)
        settings.add(mf, text="Input")
        self.var_method = tk.StringVar(value=self.cfg["input_method"])
        ttk.Label(mf, text="Code input").pack(anchor=tk.W)
        ttk.Radiobutton(mf, text="Direct Unicode (bypasses IME)",
                        variable=self.var_method, value="unicode").pack(anchor=tk.W)
        ttk.Radiobutton(mf, text="Simulated keys (may fail with Chinese IME)",
                        variable=self.var_method, value="scancode").pack(anchor=tk.W)
        ttk.Radiobutton(mf, text="Clipboard (Ctrl+V)",
                        variable=self.var_method, value="clipboard").pack(anchor=tk.W)
        self.var_submit = tk.StringVar(value=self.cfg["submit_method"])
        ttk.Label(mf, text="Submit with").pack(anchor=tk.W, pady=(4, 0))
        srow = ttk.Frame(mf)
        srow.pack(anchor=tk.W)
        ttk.Radiobutton(srow, text="Click Confirm",
                        variable=self.var_submit, value="click").pack(side=tk.LEFT)
        ttk.Radiobutton(srow, text="Press Space (failed in testing)",
                        variable=self.var_submit, value="space").pack(side=tk.LEFT, padx=6)
        self.var_continuous = tk.BooleanVar(value=self.cfg["continuous_mode"])
        ttk.Checkbutton(mf, text="Reuse the open dialog after a failed code",
                        variable=self.var_continuous).pack(anchor=tk.W, pady=(4, 0))

        tf = ttk.Frame(settings, padding=10)
        settings.add(tf, text="Timing (seconds)")
        tf.columnconfigure(0, weight=1)
        self.spins = {}
        rows = [
            ("start_countdown", "Start countdown", 0, 30, 1),
            ("delay_after_entry", "Wait after opening dialog", 0.1, 10, 0.1),
            ("key_interval", "Delay between keystrokes", 0.0, 1, 0.01),
            ("delay_after_confirm", "Result timeout", 0.1, 20, 0.1),
            ("delay_between_codes", "Delay between codes", 0, 20, 0.1),
        ]
        for i, (key, label, lo, hi, step) in enumerate(rows):
            ttk.Label(tf, text=label).grid(row=i, column=0, sticky=tk.W, pady=1)
            var = tk.StringVar(value=str(self.cfg[key]))
            ttk.Spinbox(tf, from_=lo, to=hi, increment=step, width=7, textvariable=var
                        ).grid(row=i, column=1, sticky=tk.E, padx=(8, 0))
            self.spins[key] = var

        guide = views["How to use"]
        steps = [
            ("1. Set up the game", "Choose Windowed or Borderless in the game’s display settings."),
            ("2. Find Exchange Code", "Open Settings → Other. Leave the Exchange Code row visible and the code entry dialog closed."),
            ("3. Copy active codes", "Open codes.yar.gg. Click the three-dot More tools menu, choose Copy all active codes, and confirm. Return to Code queue and click Paste."),
            ("4. Test one code", "Click Test first code only. Switch back to the game during the countdown (5 seconds by default). The app also tries to bring the game to the front."),
            ("5. Run the list", "Check Activity log for the test result. If the game received the code as expected, click Start redeeming and switch back again."),
            ("Stop at any time", "Press F12, even with the game in front, or click the floating Stop (F12) button. It usually appears at the lower right."),
        ]
        guide_text = tk.Text(guide, wrap="word", width=1, height=1, bg=paper, fg=ink,
                             relief="flat", font=("Segoe UI", 11), highlightthickness=0,
                             selectbackground=ink, selectforeground="white", padx=2)
        guide_scroll = ttk.Scrollbar(guide, orient="vertical", command=guide_text.yview)
        guide_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        guide_text.configure(yscrollcommand=guide_scroll.set)
        guide_text.pack(fill=tk.BOTH, expand=True)
        guide_text.tag_configure("heading", font=("Segoe UI", 11, "bold"), spacing1=8, spacing3=2)
        for title, detail in steps:
            guide_text.insert(tk.END, title + "\n", "heading")
            guide_text.insert(tk.END, detail + "\n")
        guide_text.configure(state=tk.DISABLED)
        ttk.Button(guide, text="Open codes.yar.gg ↗",
                   command=lambda: webbrowser.open(CODES_WEBSITE)).pack(anchor="w", pady=(6, 0))

        activity = views["Activity log"]
        self.txt_log = scrolledtext.ScrolledText(activity, height=5, width=20, font=("Consolas", 10),
                                                wrap="word", state=tk.DISABLED, **editor_colors)
        self.txt_log.pack(fill=tk.BOTH, expand=True)
        ttk.Label(activity, text="Original tool by Dazhu Titi   /   Code data curated by Ruili",
                  foreground=muted).pack(anchor="w", pady=(8, 0))
        ttk.Separator(main).pack(fill=tk.X, pady=(12, 5))
        ttk.Label(main, text="Open Settings → Other in windowed or borderless mode. Switch back during the countdown.",
                  wraplength=int(560 * self.k), foreground=muted).pack(anchor="w", pady=(2, 8))
        actions = ttk.Frame(main)
        actions.pack(fill=tk.X)
        self.btn_stop = ttk.Button(actions, text="Stop (F12)", command=self.stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT)
        self.btn_start = ttk.Button(actions, text="Start redeeming", style="Ink.TButton", command=self.start_all)
        self.btn_start.pack(side=tk.RIGHT)
        self.btn_test = ttk.Button(actions, text="Test first code only", command=self.start_test)
        self.btn_test.pack(side=tk.RIGHT, padx=8)
        self._update_count()
        show("Code queue")

    def log(self, msg):
        def _append():
            try:
                self.txt_log.configure(state=tk.NORMAL)
                self.txt_log.insert(tk.END, time.strftime("[%H:%M:%S] ") + msg + "\n")
                self.txt_log.see(tk.END)
                self.txt_log.configure(state=tk.DISABLED)
            except tk.TclError:      # 窗口已经关了
                pass
        try:
            self.root.after(0, _append)
        except (tk.TclError, RuntimeError):
            pass

    def _poll_hotkey(self):
        """F12 全局急停。GetAsyncKeyState 读的是全局键盘状态，游戏在前台时照样有效。
        除了工作线程自己会查，这里再挂一个 UI 侧的轮询，双保险。"""
        if key_pressed(VK_F12) and self.worker and self.worker.is_alive():
            self.stop()
        self.root.after(50, self._poll_hotkey)

    def _refresh_labels(self):
        w, h = screen_size()
        for key, (lbl, name) in self.pt_labels.items():
            rx, ry = self.cfg[key]
            lbl.config(text=f"{name:<8}({int(rx * w):>5},{int(ry * h):>5})")

    def _update_count(self):
        self.lbl_count.config(text=f"{len(self.get_codes())} codes")

    def get_codes(self):
        codes, seen = [], set()
        for line in self.txt_codes.get("1.0", tk.END).splitlines():
            c = line.strip().replace(" ", "")
            if not c or c.startswith("#") or c in seen:
                continue
            seen.add(c)
            codes.append(c)
        return codes

    def clear_codes(self):
        self.txt_codes.delete("1.0", tk.END)
        self._update_count()

    def paste_codes(self):
        try:
            data = self.root.clipboard_get()
        except tk.TclError:
            return
        data = data.strip()
        if not data:
            return
        # 已有内容的最后一行没换行时，直接 insert(END) 会把粘贴的码接在它后面拼成一个
        cur = self.txt_codes.get("1.0", "end-1c")
        if cur and not cur.endswith("\n"):
            self.txt_codes.insert(tk.END, "\n")
        self.txt_codes.insert(tk.END, data + "\n")
        self._update_count()

    # ---------------- 网站抓取 ----------------
    def fetch_codes(self):
        since = parse_user_date(self.var_since.get())
        if since is None:
            messagebox.showwarning("Invalid date",
                                   "Enter a start date like 2026-08-27, 2026/8/27, or 8/27.")
            return
        self.var_since.set(since.isoformat())
        self.cfg["fetch_since"] = since.isoformat()
        self.cfg["fetch_include_noexpiry"] = bool(self.var_noexpiry.get())
        self._save_cfg()

        url = self.cfg["fetch_url"]
        include_noexpiry = bool(self.var_noexpiry.get())
        self.btn_fetch.config(state=tk.DISABLED, text="Fetching...")
        self.log(f"Fetching codes posted on or after {since.isoformat()}...")

        def deliver(picked, group_count, err):
            try:
                self.root.after(0, lambda: self._fetch_done(picked, group_count, err))
            except (tk.TclError, RuntimeError):   # 抓取途中窗口被关了
                pass

        def work():
            try:
                groups = fetch_site_codes(url)
            except Exception as e:
                deliver(None, None, f"Fetch failed: {type(e).__name__}: {e}")
                return
            picked, undated = [], []
            for label, codes in groups:
                d = parse_site_date(label)
                if d is None:
                    undated.extend(codes)      # 「無時限」以及万一出现的解析不了的标签
                elif d >= since:
                    picked.extend(codes)
            if include_noexpiry:
                picked = undated + picked
            deliver(picked, len(groups), None)

        threading.Thread(target=work, daemon=True).start()

    def _fetch_done(self, picked, group_count, err):
        try:
            self.btn_fetch.config(state=tk.NORMAL, text="Fetch codes")
        except tk.TclError:
            return
        if err:
            self.log(f"⚠ {err}")
            return
        existing = set(self.get_codes())
        new, seen = [], set()
        for c in picked:
            c = c.strip().replace(" ", "")
            if c and c not in existing and c not in seen:   # 站内跨日期偶有重复码
                seen.add(c)
                new.append(c)
        if not new:
            self.log(f"All {len(picked)} matching codes are already in the list. No codes added.")
            return
        cur = self.txt_codes.get("1.0", "end-1c")
        if cur and not cur.endswith("\n"):
            self.txt_codes.insert(tk.END, "\n")
        self.txt_codes.insert(tk.END, "\n".join(new) + "\n")
        self._update_count()
        dup = len(picked) - len(new)
        self.log(f"Found {group_count} groups and {len(picked)} matching codes. "
                 f"Added {len(new)} codes." + (f" Skipped {dup} duplicates." if dup else ""))
        self.log("Review the list, then click Start redeeming.")

    def _save_cfg(self):
        if not save_config(self.cfg):
            self.log("⚠ Could not save settings. Check that the app folder is writable. Changes apply for this session.")

    def reset_points(self):
        for k in self.pt_labels:
            self.cfg[k] = list(DEFAULT_CONFIG[k])
        self._save_cfg()
        self._refresh_labels()
        self.log("Default positions restored.")

    # ---------------- 状态检测 ----------------
    def _xy(self, key):
        w, h = screen_size()
        return int(self.cfg[key][0] * w), int(self.cfg[key][1] * h)

    def in_settings_page(self):
        """True = 设置页；False = 弹窗层；None = 读不到像素（独占全屏）"""
        b = pixel_brightness(*self._xy("anchor_point"))
        return None if b is None else b >= self.cfg["anchor_threshold"]

    def on_dialog_bar(self):
        """副锚点：确认/取消按钮那一行是不是弹窗的深色底栏"""
        b = pixel_brightness(*self._xy("guard_point"))
        return None if b is None else b < self.cfg["guard_threshold"]

    def test_state(self):
        a = pixel_brightness(*self._xy("anchor_point"))
        g = pixel_brightness(*self._xy("guard_point"))
        if a is None:
            self.log("Unable to read screen pixels. Switch the game to Windowed or Borderless mode.")
            return
        self.log(f"Main anchor brightness={a} (threshold {self.cfg['anchor_threshold']}) → "
                 f"{'Settings page' if a >= self.cfg['anchor_threshold'] else 'Redeem dialog'}"
                 f"  |  Secondary anchor brightness={g} (threshold {self.cfg['guard_threshold']}) → "
                 f"{'Dialog footer' if g is not None and g < self.cfg['guard_threshold'] else 'Outside dialog footer'}")

    # ---------------- 校准 ----------------
    def calibrate(self, key, what):
        if self.worker and self.worker.is_alive():
            return
        win = tk.Toplevel(self.root)
        win.title("Calibrate position")
        win.geometry("460x180")
        win.attributes("-topmost", True)
        ttk.Label(win, padding=14, wraplength=430, justify=tk.LEFT,
                  text=f"Switch to the game and hover over:\n\n{what}\n\nPress F11 to capture, or Esc to cancel.").pack()
        ttk.Label(win, text="Waiting for F11...", foreground="#444444").pack()

        def poll():
            if not win.winfo_exists():
                return
            if key_pressed(VK_F11):
                x, y = get_cursor_pos()
                w, h = screen_size()
                # 副屏上的点会算出负数或 >1 的比例，下次启动被配置校验静默丢弃，
                # 表面记上了实际没生效。直接拒绝并说清楚。
                if not (0 <= x < w and 0 <= y < h):
                    self.log(f"⚠ ({x}, {y}) is outside the primary display. "
                             "Move the game to the primary display and calibrate again. Position was not saved.")
                    win.destroy()
                    return
                self.cfg[key] = [round(x / w, 4), round(y / h, 4)]
                self._save_cfg()
                self._refresh_labels()
                b = pixel_brightness(x, y)
                self.log(f"Captured {what} → ({x}, {y})" + (f", brightness {b}" if b is not None else ""))
                win.destroy()
                return
            if key_pressed(VK_ESC):
                win.destroy()
                return
            win.after(60, poll)

        win.after(300, poll)  # 稍等一下，避免立刻读到还按着的键

    # ---------------- 执行 ----------------
    def _collect_settings(self):
        for key, var in self.spins.items():
            try:
                val = float(var.get())
            except ValueError:
                continue
            if val < 0:       # 负的延时/次数没有意义，保留原值
                continue
            self.cfg[key] = int(val) if isinstance(DEFAULT_CONFIG[key], int) else val
        self.cfg["input_method"] = self.var_method.get()
        self.cfg["submit_method"] = self.var_submit.get()
        self.cfg["continuous_mode"] = bool(self.var_continuous.get())
        self._save_cfg()

    # ---- 悬浮停止条 ----
    # 主窗口不能整个置顶：它 920x720 摆在左上角，正好把主锚点盖住，
    # 读像素就读到 GUI 自己的界面了，状态检测直接失效。
    # 所以只浮一个小条，并且自动挑一个不压住任何校准点的角落。
    def _overlay_pos(self, w, h):
        sw, sh = screen_size()
        pts = [self._xy(k) for k in self.pt_labels]
        m = 12
        for x, y in [(sw - w - m, sh - h - m), (m, sh - h - m),
                     (sw - w - m, m), (m, m)]:
            if not any(x - 10 <= px <= x + w + 10 and y - 10 <= py <= y + h + 10
                       for px, py in pts):
                return x, y
        return sw - w - m, sh - h - m   # 六个点把四角都占了，几乎不可能

    def _show_overlay(self):
        w, h = int(300 * self.k), int(104 * self.k)
        x, y = self._overlay_pos(w, h)
        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)          # 无标题栏，不抢焦点
        ov.attributes("-topmost", True)
        ov.geometry(f"{w}x{h}+{x}+{y}")
        ov.configure(bg="#1b1b1b")
        self.ov_status = tk.Label(ov, text="Getting ready...", bg="#1b1b1b", fg="#eee",
                                  font=("Segoe UI", 10))
        self.ov_status.pack(pady=(8, 4))
        tk.Button(ov, text="■ Stop (F12)", command=self.stop,
                  bg="white", fg="#171717", activebackground="#dddddd", font=("Segoe UI", 11, "bold"),
                  relief=tk.FLAT, cursor="hand2"
                  ).pack(fill=tk.X, padx=10, pady=(0, 10), ipady=4)
        self.overlay = ov

    def _hide_overlay(self):
        if getattr(self, "overlay", None):
            try:
                self.overlay.destroy()
            except tk.TclError:
                pass
            self.overlay = None
            self.ov_status = None

    def _set_status(self, text):
        def _apply():
            if getattr(self, "ov_status", None):
                try:
                    self.ov_status.config(text=text)
                except tk.TclError:
                    pass
        try:
            self.root.after(0, _apply)
        except (tk.TclError, RuntimeError):   # 主窗口已关
            pass

    def start_all(self):
        self._start(self.get_codes())

    def start_test(self):
        self._start(self.get_codes()[:1])

    def _start(self, codes):
        if self.worker and self.worker.is_alive():
            return
        if not codes:
            messagebox.showwarning("No codes", "Enter one redemption code per line in the list.")
            return
        st = self.in_settings_page()
        if st is None:
            messagebox.showerror(
                "Cannot read screen pixels",
                "The game appears to be in exclusive fullscreen. The tool cannot identify the current screen.\n\n"
                "The Confirm and Cancel positions overlap the Challenge Lock setting when the redeem dialog is closed. "
                "The run was blocked to avoid changing your settings.\n\n"
                "Switch the game to Windowed or Borderless mode and try again.")
            return
        if st is False:
            # 独占全屏在某些机器上读回来的是全黑而不是「读不到」——全黑会被判成
            # 「弹窗层」，两道点击校验全过，会直接盲点。要求开始时必须检测到亮色
            # 设置页，同时把这种情况和「残留弹窗没关」一起挡在门外。
            messagebox.showerror(
                "Settings page not detected",
                "Open Settings > Other before starting. The page should have a light background.\n\n"
                "If the redeem dialog is still open, click Cancel in the game. "
                "If you are in exclusive fullscreen, switch to Windowed or Borderless mode. "
                "If the Settings page is already open, check the screen state "
                "and recalibrate the main anchor or check its threshold.")
            return
        if not self.admin:
            if not messagebox.askyesno(
                    "Administrator permission warning",
                    "This tool is running without administrator privileges. If the game runs as administrator, "
                    "Windows may silently block simulated input.\n\nContinue anyway?"):
                return
        self._collect_settings()
        self.stop_flag.clear()
        self.btn_start.config(state=tk.DISABLED)
        self.btn_test.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self._show_overlay()   # 悬浮停止条盖在全屏游戏上面，随时点得到
        self.worker = threading.Thread(target=self._run, args=(codes,), daemon=True)
        self.worker.start()

    def stop(self):
        if not self.stop_flag.is_set():
            self.stop_flag.set()
            self.log("⏹ Stop requested. Finishing the current step...")

    def _finish(self):
        self.btn_start.config(state=tk.NORMAL)
        self.btn_test.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self._hide_overlay()

    # ---- 中断检查：所有会动鼠标键盘的地方之前都要过一遍 ----
    def _guard(self):
        if self.stop_flag.is_set() or key_pressed(VK_F12):
            self.stop_flag.set()
            raise Aborted()

    def _sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self._guard()
            time.sleep(0.03)

    def _click(self, key):
        self._guard()
        move_and_click(*self._xy(key))

    def _click_dialog_button(self, key, name):
        """
        点弹窗底部的确认/取消按钮。这两个坐标在设置页上压着「挑战锁」的开启按钮，
        点错一次就改了玩家的账号设置，所以两道校验都过了才动手，任何一道不过就中止。
        """
        self._guard()
        if self.in_settings_page() is not False:
            raise UnsafeState(f"Stopped before clicking {name}: the redeem dialog is no longer visible.")
        if self.on_dialog_bar() is not True:
            raise UnsafeState(f"Stopped before clicking {name}: its row failed the dialog footer check.")
        move_and_click(*self._xy(key))

    def _clear_input(self):
        self._guard()
        m = self.cfg["clear_method"]
        if m == "backspace":
            for _ in range(int(self.cfg["backspace_times"])):
                self._guard()
                key_tap(SC_BACKSPACE, hold=0.01)
                time.sleep(0.012)
        elif m == "ctrl_a":
            key_combo(SC_LCTRL, SC_A)
            time.sleep(0.1)
            key_tap(SC_DELETE, extended=True)

    def _type_code(self, code):
        m = self.cfg["input_method"]
        if m == "clipboard":
            # 剪贴板可能被别的程序占用。失败时绝不能照按 Ctrl+V——
            # 粘进去的会是剪贴板里残留的旧内容。降级走 Unicode 直发。
            if set_clipboard(code):
                time.sleep(0.15)
                self._guard()
                key_combo(SC_LCTRL, SC_V)
                return
            self.log("    Clipboard unavailable. Using direct Unicode for this code.")
            m = "unicode"
        for ch in code:
            self._guard()
            if m == "unicode":
                type_char_unicode(ch)
            else:
                type_char_scan(ch)
            time.sleep(self.cfg["key_interval"])

    def _wait_result(self, timeout):
        """
        成功 → 游戏自己关掉弹窗，回到设置页；失败 → 弹窗一直开着。
        返回 'success' / 'failed'
        """
        end = time.time() + timeout
        hits = 0
        while time.time() < end:
            self._guard()
            # 连续两次读到亮色才认成功，避开关闭动画途中的误判
            hits = hits + 1 if self.in_settings_page() else 0
            if hits >= 2:
                return "success"
            time.sleep(0.15)
        return "failed"

    def _back_to_settings(self):
        """点「取消」退回设置页。已经在设置页就什么都不做——绝不多点一下。"""
        for i in range(3):
            self._guard()
            if self.in_settings_page():
                return
            self._click_dialog_button("point_cancel", "Esc Cancel")
            self._sleep(self.cfg["delay_after_cancel"])
        if not self.in_settings_page():
            raise UnsafeState("Stopped after three Cancel attempts. The Settings page did not return.")

    def _open_dialog(self):
        """点开兑换弹窗，并确认真的开了"""
        for i in range(2):
            self._click("point_entry")
            self._sleep(self.cfg["delay_after_entry"])
            if self.in_settings_page() is False:
                return
            self.log("    The redeem dialog did not open. Retrying...")
        raise UnsafeState("Stopped after two attempts to open the redeem dialog. Check the Redeem Code button position.")

    def _do_one(self, code):
        cfg = self.cfg
        # 1. 确保有一个开着的弹窗
        if cfg["continuous_mode"] and self.in_settings_page() is False:
            self.log("    The dialog is still open. Entering the next code directly.")
        else:
            self._back_to_settings()
            self._open_dialog()

        # 2. 聚焦输入框并清空（失败时里面还留着上一个码）
        self._click("point_input")
        self._sleep(cfg["delay_after_click_input"])
        self._clear_input()
        self._sleep(0.15)

        # 3. 输入
        self._type_code(code)
        self._sleep(cfg["delay_before_submit"])

        # 4. 提交
        if cfg["submit_method"] == "space":
            self._guard()
            key_tap(SC_SPACE, hold=0.05)
        else:
            self._click_dialog_button("point_ok", "Space Confirm")

        # 5. 看弹窗关没关，就知道成功没成功
        return self._wait_result(cfg["delay_after_confirm"])

    def _run(self, codes):
        cfg = self.cfg
        ok, used = [], []
        done = 0
        head = "Finished"
        try:
            self.log(f"Starting {len(codes)} codes in {cfg['start_countdown']} seconds. Switch back to the game.")
            for i in range(int(cfg["start_countdown"]), 0, -1):
                self.log(f"  {i} ...")
                self._set_status(f"Starting in {i} s. Switch back to the game.")
                self._sleep(1)

            # 倒计时结束主动把游戏带回前台，用户忘了切也不至于点到别的窗口上
            self._guard()
            fg = bring_game_foreground(cfg["game_window_title"])
            if fg is None:
                self.log(f"⚠ Window titled {cfg['game_window_title']} was not found. "
                         "Switch back manually or set game_window_title in config.json.")
            elif not fg:
                self.log("⚠ Could not switch to the game window. Switch back manually.")
            else:
                self.log("Switched to the game window.")

            # 此刻游戏应在前台、画面应是亮色设置页。点开始时主窗口可能恰好盖住锚点，
            # 前置检查读到的是本程序自己的浅色界面——所以这里必须以游戏画面重新验一次，
            # 不通过就在点出任何一下鼠标之前中止。
            if self.in_settings_page() is not True:
                raise UnsafeState("The light Settings page was not detected after switching back. "
                                  "Open Settings > Other, close any dialog, and use Windowed or Borderless mode.")

            for idx, code in enumerate(codes, 1):
                self.log(f"[{idx}/{len(codes)}] {code}")
                self._set_status(f"[{idx}/{len(codes)}]  {code}")
                result = self._do_one(code)
                if result == "success":
                    ok.append(code)
                    self.log("    ✓ Redeemed")
                else:
                    used.append(code)
                    self.log("    ✗ Used or invalid")
                done += 1
                if idx < len(codes):
                    self._sleep(cfg["delay_between_codes"])

            # 最后一个码兑完（尤其是失败的情况）弹窗还开着，收个尾让游戏停回设置页
            self._back_to_settings()
            head = "Completed"
        except Aborted:
            head = "Stopped"
        except UnsafeState as e:
            self.log(f"⚠ {e}")
            head = "Aborted"
        except Exception as e:
            self.log(f"⚠ Error: {type(e).__name__}: {e}")
            head = "Aborted"
        finally:
            # 收尾必须无条件执行：漏掉一次，悬浮停止条就关不掉、按钮永久禁用，
            # 界面等于卡死。所以哪怕上面写日志本身出错也要保证走到这里。
            try:
                self.log(f"{head}. Processed {done}/{len(codes)} codes. "
                         f"Redeemed: {len(ok)}. Used or invalid: {len(used)}.")
                if ok:
                    self.log("  Redeemed: " + "  ".join(ok))
                    self.log("  Collect your rewards from in-game mail.")
                if used:
                    self.log("  Used or invalid: " + "  ".join(used))
            except Exception:
                pass
            try:
                self.root.after(0, self._finish)
            except (tk.TclError, RuntimeError):   # 主窗口在运行中被关掉
                pass


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
