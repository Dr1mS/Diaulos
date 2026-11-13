"""Session fixtures for the vibe tests: sets of vibe 3 session folders,
written into a temporary save_dir with times relative to now.

Layout and fields are those of vibe 3.1.2 (probes T10-vibe3/14 and 20):
<save_dir>/unified/<session id>/{meta.json,CURRENT,...}, meta.json with session_id,
parent_session_id, start_time, end_time, title (None until set), title_source,
environment.working_directory, child_sessions. vibe 3 keeps the id through
/compact and --resume, and /clear starts a new id with a new suffix. Each
folder also gets an empty messages.jsonl, only for the fake vibe-acp of the
vibe 2.20 runner tests, which appends to it.

Usage:
    from tests.fakes.vibe_sessions.build import build, EXPECTED
    ids, folders = build(save_dir, workspace)   # {"compacted": [id], ...}

EXPECTED says, per case, what discovery should report: one conversation per
suffix, its current session (latest end_time), its name, whether it is listed.
"""
import json
import os
from datetime import datetime, timedelta, timezone

# suffix (12 hex) per conversation; fixed, so tests can name addresses.
SUFFIX = {
    "compacted": "c0a1c0a1c0a1",
    "before_clear": "c1ea2c1ea2c1",
    "after_clear": "a7c1ea2a7c1e",
    "resumed": "e5e5e5e5e5e5",
    "subagent": "5ab5ab5ab5ab",
    "renamed": "4e4a4e4a4e4a",
    "old": "01d01d01d01d",
    "twice": "7b1c07b1c07b",
}

# (case, head of the id, parent: None or (case, index), minutes ago start, minutes ago end,
#  title, title_source)
SESSIONS = [
    # /compact keeps the id: one session, its title still auto (None until vibe sets one).
    ("compacted", "aaaaaaa1-0000-0000-0001", None, 60, 5, None, "auto"),
    # /clear: a new id with a new suffix, so two conversations.
    ("before_clear", "bbbbbbb1-0000-0000-0001", None, 90, 80, "before clear", "auto"),
    ("after_clear", "bbbbbbb2-0000-0000-0002", None, 79, 70, "after clear", "auto"),
    # --resume of an older session keeps the id: an old start, the latest end_time.
    ("resumed", "ccccccc1-0000-0000-0001", None, 300, 2, "resumed later", "auto"),
    # a subagent: its parent has another suffix.
    ("subagent", "ddddddd1-0000-0000-0001", ("compacted", 0), 10, 9, "subagent task", "auto"),
    # /rename: manual title, with a character outside [A-Za-z0-9_.-].
    ("renamed", "eeeeeee1-0000-0000-0001", None, 30, 20, "review bot", "manual"),
    # beyond RECENT_DAYS (7): not listed without days/all.
    ("old", "fffffff1-0000-0000-0001", None, 30 * 24 * 60 + 10, 30 * 24 * 60, "old one", "auto"),
    # two ids with one suffix (not seen in vibe 3, kept as a guard): the one saved last wins.
    ("twice", "9999999a-0000-0000-0001", None, 120, 3, "saved last", "auto"),
    ("twice", "9999999b-0000-0000-0002", None, 110, 100, "saved first", "auto"),
]

EXPECTED = {
    "compacted": {"listed": True, "current": 0, "name": None, "auto_title": None},
    "before_clear": {"listed": True, "current": 0, "name": None, "auto_title": "before clear"},
    "after_clear": {"listed": True, "current": 0, "name": None, "auto_title": "after clear"},
    "resumed": {"listed": True, "current": 0, "name": None, "auto_title": "resumed later"},
    "subagent": {"listed": False},
    "renamed": {"listed": True, "current": 0, "name": "review_bot", "auto_title": None},
    "old": {"listed": False, "listed_with_all": True, "current": 0},
    "twice": {"listed": True, "current": 0, "name": None, "auto_title": "saved last"},
}


def _iso(minutes_ago, now):
    return (now - timedelta(minutes=minutes_ago)).isoformat()


def build(save_dir, workspace, now=None):
    """Write every fixture session under save_dir/unified, with the files vibe
    keeps beside them (.session-index.json, active/). Returns {case: [session
    ids]} and {case: [folders]} as (ids, folders)."""
    now = now or datetime.now(timezone.utc)
    root = os.path.join(save_dir, "unified")
    os.makedirs(root, exist_ok=True)
    os.makedirs(os.path.join(save_dir, "active"), exist_ok=True)
    ids, folders = {}, {}
    for case, head, parent, start_ago, end_ago, title, source in SESSIONS:
        sid = "%s-%s" % (head, SUFFIX[case])
        parent_id = ids[parent[0]][parent[1]] if parent else None
        folder = os.path.join(root, sid)
        os.makedirs(folder)
        meta = {
            "session_id": sid,
            "parent_session_id": parent_id,
            "start_time": _iso(start_ago, now),
            "end_time": _iso(end_ago, now),
            "git_commit": None,
            "git_branch": None,
            "environment": {"working_directory": workspace},
            "username": "<fake>",
            "child_sessions": [],
            "title": title,
            "title_source": source,
        }
        with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        with open(os.path.join(folder, "CURRENT"), "w", encoding="utf-8") as f:
            json.dump({"generation": "0000000000000001", "session_id": sid, "snapshot_sequence": 1,
                       "store_format": "mistral.vibe.unified-session-store/v1"}, f)
        open(os.path.join(folder, "messages.jsonl"), "w").close()
        ids.setdefault(case, []).append(sid)
        folders.setdefault(case, []).append(folder)
    with open(os.path.join(root, ".session-index.json"), "w", encoding="utf-8") as f:
        json.dump({"catalog_version": 1, "entries": [
            {"identity": {"depth": 0, "kind": "root", "session_id": sid}}
            for v in ids.values() for sid in v]}, f)
    # A folder vibe left without a readable meta.json, and one whose meta.json names another id.
    os.makedirs(os.path.join(root, "0badbad0-0000-0000-0000-000000000000"))
    stray = os.path.join(root, "0badbad1-0000-0000-0000-000000000001")
    os.makedirs(stray)
    with open(os.path.join(stray, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"session_id": ids["renamed"][0]}, f)
    return ids, folders
