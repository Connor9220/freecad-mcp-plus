# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# External XTest typing, run OUTSIDE FreeCAD (rescues a bridge call stuck in a modal):
#   DISPLAY=:1 python3 xkeys_external.py "text" [@enter|@tab|@esc ...]   ("@name" = key name)
import ctypes
import sys
import time

x = ctypes.cdll.LoadLibrary("libX11.so.6")
t = ctypes.cdll.LoadLibrary("libXtst.so.6")
x.XOpenDisplay.restype = ctypes.c_void_p
x.XStringToKeysym.restype = ctypes.c_ulong
x.XStringToKeysym.argtypes = [ctypes.c_char_p]
x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
d = ctypes.c_void_p(x.XOpenDisplay(None))
CH = {" ": "space", ".": "period", "-": "minus", "/": "slash"}
NM = {"enter": "Return", "tab": "Tab", "esc": "Escape"}


def k(name):
    c = x.XKeysymToKeycode(d, x.XStringToKeysym(name.encode()))
    t.XTestFakeKeyEvent(d, c, True, 0)
    x.XFlush(d)
    time.sleep(0.03)
    t.XTestFakeKeyEvent(d, c, False, 0)
    x.XFlush(d)
    time.sleep(0.06)


for a in sys.argv[1:]:
    if a.startswith("@"):
        k(NM.get(a[1:], a[1:]))
        time.sleep(0.3)
    else:
        for ch in a:
            k(CH.get(ch, ch))
        time.sleep(0.3)
