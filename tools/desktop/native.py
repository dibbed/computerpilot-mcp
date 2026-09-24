"""Minimal Windows input and foreground-window primitives using ctypes."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from typing import Any

from core.errors import ToolError


def require_windows() -> None:
    if os.name != "nt":
        raise ToolError("windows_only", "Desktop automation requires Windows.")


ULONG_PTR = wintypes.WPARAM


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


_USER32: Any = None
if os.name == "nt":
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise OSError("ctypes.WinDLL is unavailable on this Windows runtime.")
    _USER32 = win_dll("user32", use_last_error=True)
    _USER32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _USER32.SendInput.restype = wintypes.UINT
    _USER32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
    _USER32.SetCursorPos.restype = wintypes.BOOL
    _USER32.VkKeyScanW.argtypes = (wintypes.WCHAR,)
    _USER32.VkKeyScanW.restype = ctypes.c_short
    _USER32.GetForegroundWindow.argtypes = ()
    _USER32.GetForegroundWindow.restype = wintypes.HWND
    _USER32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    _USER32.GetWindowTextLengthW.restype = ctypes.c_int
    _USER32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    _USER32.GetWindowTextW.restype = ctypes.c_int
    _USER32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    _USER32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _USER32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    _USER32.GetWindowRect.restype = wintypes.BOOL


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSE_FLAGS = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
SPECIAL_KEYS = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "win": 0x5B,
    "windows": 0x5B,
}
SPECIAL_KEYS.update({f"f{index}": 0x6F + index for index in range(1, 25)})


def _user32() -> Any:
    require_windows()
    assert _USER32 is not None
    return _USER32


def _win_error() -> OSError:
    factory = getattr(ctypes, "WinError", None)
    return factory() if callable(factory) else OSError("Windows API call failed.")


def _send_input(value: INPUT) -> None:
    sent = _user32().SendInput(1, ctypes.byref(value), ctypes.sizeof(INPUT))
    if sent != 1:
        raise _win_error()


def _send_key(vk: int, key_up: bool = False) -> None:
    flags = KEYEVENTF_KEYUP if key_up else 0
    _send_input(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, 0, flags, 0, 0)))


def type_unicode(text: str, interval_ms: int) -> None:
    require_windows()
    encoded = text.encode("utf-16-le")
    for index in range(0, len(encoded), 2):
        unit = int.from_bytes(encoded[index : index + 2], "little")
        _send_input(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, unit, KEYEVENTF_UNICODE, 0, 0)))
        _send_input(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)))
        if interval_ms:
            time.sleep(interval_ms / 1_000)


def virtual_key(name: str) -> int:
    lowered = name.casefold()
    if lowered in SPECIAL_KEYS:
        return SPECIAL_KEYS[lowered]
    if len(name) == 1:
        value = _user32().VkKeyScanW(name)
        if value != -1:
            return value & 0xFF
    raise ToolError("unknown_key", f"Unsupported hotkey name: {name}")


def send_hotkey(keys: list[str], hold_ms: int) -> None:
    require_windows()
    codes = [virtual_key(key) for key in keys]
    for code in codes:
        _send_key(code)
    if hold_ms:
        time.sleep(hold_ms / 1_000)
    for code in reversed(codes):
        _send_key(code, key_up=True)


def click_mouse(x: int, y: int, button: str, clicks: int, interval_ms: int) -> None:
    require_windows()
    if not _user32().SetCursorPos(x, y):
        raise _win_error()
    down, up = MOUSE_FLAGS[button]
    for index in range(clicks):
        _send_input(INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, down, 0, 0)))
        _send_input(INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, up, 0, 0)))
        if interval_ms and index + 1 < clicks:
            time.sleep(interval_ms / 1_000)


def foreground_window() -> dict[str, Any]:
    require_windows()
    user32 = _user32()
    handle = user32.GetForegroundWindow()
    if not handle:
        raise ToolError("no_active_window", "Windows did not report a foreground window.")
    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    pid = wintypes.DWORD()
    thread_id = user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    rect = wintypes.RECT()
    if not user32.GetWindowRect(handle, ctypes.byref(rect)):
        raise _win_error()
    return {
        "ok": True,
        "handle": int(handle),
        "title": buffer.value,
        "pid": int(pid.value),
        "thread_id": int(thread_id),
        "rect": {"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom},
    }
