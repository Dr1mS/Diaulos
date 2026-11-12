#!/usr/bin/env python3
"""Render the current screen of a pty_drive.py run as text.

Usage: screen.py <control-dir>   (needs the `pyte` terminal emulator:
python3 -m venv v && v/bin/pip install pyte, then run it with v/bin/python)

Replays <control-dir>/tty.raw into a 160x50 virtual terminal and prints what
a person would see on screen now, trailing blanks removed.
"""

import os
import sys

import pyte

screen = pyte.Screen(160, 50)
stream = pyte.ByteStream(screen)
with open(os.path.join(sys.argv[1], "tty.raw"), "rb") as f:
    stream.feed(f.read())
lines = [line.rstrip() for line in screen.display]
while lines and not lines[-1]:
    lines.pop()
print("\n".join(lines))
