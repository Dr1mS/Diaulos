#!/usr/bin/env python3
"""For gate R1 (tests/vibe3/gates_relay.py): reads lines on stdin and prints, for each, the time it was
read (time.time()), one per line, at end of stdin."""
import sys
import time

stamps = []
for _ in iter(sys.stdin.buffer.readline, b""):
    stamps.append(time.time())
sys.stdout.write("\n".join("%.6f" % s for s in stamps) + "\n")
