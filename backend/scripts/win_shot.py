"""Capture the whole desktop or a single window to a PNG (Windows, no deps).

python scripts/win_shot.py --out D:\\CodeSplit\\.shots\\x.png
python scripts/win_shot.py --out x.png --window CodeSlice
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import sys


def find_window(title_part: str) -> int:
    user32 = ctypes.windll.user32
    matches: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def enum(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if title_part.lower() in buf.value.lower():
            matches.append((hwnd, buf.value))
        return True

    user32.EnumWindows(enum, 0)
    if not matches:
        raise SystemExit(f"no visible window matching {title_part!r}")
    return matches[0][0]


def rect_of(hwnd: int) -> tuple[int, int, int, int]:
    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    user32 = ctypes.windll.user32
    user32.SetForegroundWindow(hwnd)
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    rect = RECT()
    ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)
    )  # DWMWA_EXTENDED_FRAME_BOUNDS
    if rect.right - rect.left <= 0:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def capture(x: int, y: int, w: int, h: int, out: str) -> None:
    from PIL import ImageGrab

    img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    img.save(out)
    print(f"saved {out} ({w}x{h})")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # Without this the window rect is logical but ImageGrab returns physical
    # pixels, so the crop lands on the wrong region on scaled displays.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--window", default="CodeSlice")
    ap.add_argument("--delay", type=float, default=0.6)
    args = ap.parse_args()
    import time

    hwnd = find_window(args.window)
    x, y, w, h = rect_of(hwnd)
    time.sleep(args.delay)
    capture(x, y, w, h, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
