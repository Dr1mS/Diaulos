#!/usr/bin/env python3
"""Tui.close really closes the terminal (T24). Linux only, no vibe, no model call.

    python3 tests/vibe3/tui_close_check.py [--passes 3]

Each pass opens `bash --norc -i` in a Tui (tui_probe.py), then calls Tui.close(): the reader is
stopped, then the PTY master is closed. bash must exit within 1 s. Exit code 0 when it does in
every pass. (Before T24 the reader was blocked in os.read and kept the PTY open as long as no
output came: an idle bash like this one outlived os.close(tui.fd). With vibe writing to the
terminal, the read returned and the PTY hung up within 0.4 s.)
"""
import argparse
import os
import signal
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tui_probe import Tui  # noqa: E402


def exit_after(pid, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.waitpid(pid, os.WNOHANG)[0]:
            return time.time() - t0
        time.sleep(0.02)
    return None


def one():
    tui = Tui(tempfile.gettempdir(), {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, ["--norc", "-i"],
              command=["/bin/bash"])
    time.sleep(1)
    t0 = time.time()
    tui.close()
    took = exit_after(tui.pid, 5 - (time.time() - t0))
    if took is None:
        os.kill(tui.pid, signal.SIGKILL)
        os.waitpid(tui.pid, 0)
    return took


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--passes", type=int, default=3)
    args = ap.parse_args()
    ok = True
    for i in range(args.passes):
        took = one()
        print("Tui.close() pass %d: bash %s" % (i + 1, "exited %.2f s after the close" % took
                                                  if took is not None else "still running 5 s after the close"),
              flush=True)
        ok = ok and took is not None and took < 1
    print("Tui.close(): bash exited within 1 s in every pass: %s" % ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
