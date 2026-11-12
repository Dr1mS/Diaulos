#!/usr/bin/env python3
"""Run an interactive program (vibe's TUI) in a pseudo-terminal, without a
human. Standard library only, Linux.

Usage: pty_drive.py <control-dir> <seconds> -- <command> [args...]

- Everything the program prints goes, raw, to <control-dir>/tty.raw.
- When a file <control-dir>/keys appears, its bytes are typed into the
  terminal and the file is removed. "\\r" in the file means Enter.
- The program is stopped (SIGHUP, then SIGKILL) after <seconds>, or when a
  file <control-dir>/stop appears.
- The child's pid is written to <control-dir>/child.pid.
"""

import os
import pty
import select
import signal
import struct
import sys
import termios
import fcntl
import time


def main():
    ctl, seconds = sys.argv[1], float(sys.argv[2])
    cmd = sys.argv[sys.argv.index("--") + 1:]
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.execvp(cmd[0], cmd)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 160, 0, 0))
    with open(os.path.join(ctl, "child.pid"), "w") as f:
        f.write(str(pid))
    raw = open(os.path.join(ctl, "tty.raw"), "ab")
    keys = os.path.join(ctl, "keys")
    stop = os.path.join(ctl, "stop")
    end = time.time() + seconds
    while time.time() < end and not os.path.exists(stop):
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            raw.write(data)
            raw.flush()
        if os.path.exists(keys):
            with open(keys, "rb") as f:
                payload = f.read().replace(b"\\r", b"\r")
            os.remove(keys)
            for b in payload:  # type one byte at a time, like a person
                os.write(fd, bytes([b]))
                time.sleep(0.02)
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        if wpid:
            break
    try:
        os.kill(pid, signal.SIGHUP)
        time.sleep(2)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    raw.close()


if __name__ == "__main__":
    main()
