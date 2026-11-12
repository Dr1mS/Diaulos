"""Session fixtures for the vibe tests: sets of vibe 2.20 session folders,
written into a temporary save_dir with times relative to now.

Layout and fields are those of vibe 2.20 (probes in prototypes/vibe*/):
<save_dir>/session_<YYYYMMDD_HHMMSS>_<first 8 hex>/{meta.json,messages.jsonl},
meta.json with session_id, parent_session_id, start_time, end_time,
title, title_source, environment.working_directory, stats.session_cost.
A subagent saves under <parent session folder>/agents/ (vibe task.py:120-125).

Usage:
    from tests.fakes.vibe_sessions.build import build, EXPECTED
    ids = build(save_dir, workspace)   # {"compacted": [id, id], ...}

EXPECTED says, per case, what discovery should report: one conversation per
suffix, its current session (latest end_time), its name, whether it is listed.
"""
import json
import os
from datetime import datetime, timedelta, timezone

# suffix (12 hex) per conversation; fixed, so tests can name addresses.
SUFFIX = {
    "compacted": "c0a1c0a1c0a1",
    "cleared": "c1ea2c1ea2c1",
    "resumed": "e5e5e5e5e5e5",
    "subagent": "5ab5ab5ab5ab",
    "renamed": "4e4a4e4a4e4a",
    "old": "01d01d01d01d",
}

# (case, head of the id, parent: None or (case, index), minutes ago start, minutes ago end,
#  title, title_source, under: None or (case, index) for <parent folder>/agents/)
SESSIONS = [
    # /compact twice: three sessions, same suffix, each with the previous one as parent.
    ("compacted", "aaaaaaa1-0000-0000-0001", None, 60, 50, "first prompt of compacted", "auto", None),
    ("compacted", "aaaaaaa2-0000-0000-0002", ("compacted", 0), 50, 40, "You are continuing a trajectory", "auto", None),
    ("compacted", "aaaaaaa3-0000-0000-0003", ("compacted", 1), 40, 5, "You are continuing a trajectory", "auto", None),
    # /clear: same suffix, no parent.
    ("cleared", "bbbbbbb1-0000-0000-0001", None, 90, 80, "before clear", "auto", None),
    ("cleared", "bbbbbbb2-0000-0000-0002", None, 79, 70, "after clear", "auto", None),
    # /resume of an older session: the older start has the latest end_time.
    ("resumed", "ccccccc1-0000-0000-0001", None, 300, 2, "resumed later", "auto", None),
    ("resumed", "ccccccc2-0000-0000-0002", ("resumed", 0), 200, 150, "You are continuing a trajectory", "auto", None),
    # subagents: one at top level whose parent has another suffix, one under agents/.
    ("subagent", "ddddddd1-0000-0000-0001", ("compacted", 2), 10, 9, "subagent task", "auto", None),
    ("subagent", "ddddddd2-0000-0000-0002", ("compacted", 2), 8, 7, "subagent task", "auto", ("compacted", 2)),
    # /rename: manual title, with a character outside [A-Za-z0-9_.-].
    ("renamed", "eeeeeee1-0000-0000-0001", None, 30, 20, "review bot", "manual", None),
    # beyond RECENT_DAYS (7): not listed without days/all.
    ("old", "fffffff1-0000-0000-0001", None, 30 * 24 * 60 + 10, 30 * 24 * 60, "old one", "auto", None),
]

EXPECTED = {
    "compacted": {"listed": True, "current": 2, "name": None, "auto_title": "You are continuing a trajectory"},
    "cleared": {"listed": True, "current": 1, "name": None, "auto_title": "after clear"},
    "resumed": {"listed": True, "current": 0, "name": None, "auto_title": "resumed later"},
    "subagent": {"listed": False},
    "renamed": {"listed": True, "current": 0, "name": "review_bot", "auto_title": None},
    "old": {"listed": False, "listed_with_all": True, "current": 0},
}


def _iso(minutes_ago, now):
    return (now - timedelta(minutes=minutes_ago)).isoformat()


def build(save_dir, workspace, now=None):
    """Write every fixture session under save_dir. Returns {case: [session ids]}
    and {case: [folders]} as (ids, folders)."""
    now = now or datetime.now(timezone.utc)
    os.makedirs(save_dir, exist_ok=True)
    ids, folders = {}, {}
    for case, head, parent, start_ago, end_ago, title, source, under in SESSIONS:
        sid = "%s-%s" % (head, SUFFIX[case])
        parent_id = ids[parent[0]][parent[1]] if parent else None
        stamp = (now - timedelta(minutes=start_ago)).strftime("%Y%m%d_%H%M%S")
        root = os.path.join(folders[under[0]][under[1]], "agents") if under else save_dir
        folder = os.path.join(root, "session_%s_%s" % (stamp, sid[:8]))
        os.makedirs(folder)
        meta = {
            "session_id": sid,
            "parent_session_id": parent_id,
            "start_time": _iso(start_ago, now),
            "end_time": _iso(end_ago, now),
            "environment": {"working_directory": workspace},
            "username": "<fake>",
            "title": title,
            "title_source": source,
            "stats": {"steps": 1, "session_cost": 0.01},
            "total_messages": 2,
        }
        with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        with open(os.path.join(folder, "messages.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"role": "user", "content": title}) + "\n")
            f.write(json.dumps({"role": "assistant", "content": "ok"}) + "\n")
        ids.setdefault(case, []).append(sid)
        folders.setdefault(case, []).append(folder)
    return ids, folders
