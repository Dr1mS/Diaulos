"""Run unittest on Linux with os.kill(pid, 0) turned into a failure (task T5c).

On Windows, signal 0 is CTRL_C_EVENT: os.kill(pid, 0) sends Ctrl-C to the whole console and
stops the test run with KeyboardInterrupt (Windows CI run of 12 Nov. 2026). This guard finds
such a call in the tests that also run on Windows, without a Windows machine:

    cd tests && python3 windows_kill_guard.py test_mailbox test_vibe_sessions test_vibe_runner
"""
import os
import sys
import unittest

_real_kill = os.kill


def _kill(pid, sig):
    if sig == 0:
        raise AssertionError("os.kill(%r, 0): a Ctrl-C to the whole console on Windows" % pid)
    return _real_kill(pid, sig)


if __name__ == "__main__":
    os.kill = _kill
    sys.path.insert(0, os.getcwd())
    sys.argv = ["python -m unittest"] + sys.argv[1:]
    unittest.main(module=None)
