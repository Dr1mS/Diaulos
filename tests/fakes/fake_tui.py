#!/usr/bin/env python3
"""Plays vibe-rs as the parent of the relay (tests/test_vibe_relay.py, T18):

    fake_tui.py <marker file> <relay command...>

Starts the relay with pipes on its stdin and stdout, as vibe-rs does, and prints "ready <relay pid>".
On SIGHUP it writes "hup" to the marker file, closes the relay's stdin and exits 0.
"""
import os
import signal
import subprocess
import sys
import time

marker, command = sys.argv[1], sys.argv[2:]
relay = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)


def on_hup(signum, frame):
    with open(marker, "a", encoding="utf-8") as f:
        f.write("hup\n")
    relay.stdin.close()
    os._exit(0)


signal.signal(signal.SIGHUP, on_hup)
print("ready %d" % relay.pid, flush=True)
while True:
    time.sleep(0.2)
