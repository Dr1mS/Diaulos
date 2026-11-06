#!/usr/bin/env python3
"""Diaulos remote: messages between bridges on different machines, through a relay.

Two machines pair once, by hand. After that, an agent on one machine writes to
`remote:<peer>/<address>` and the message reaches that session on the other
machine, whatever network each one is on. Both machines only open outgoing
HTTPS connections to a relay: ntfy.sh by default, or one you host yourself
with `remote relay-server`. The relay never sees a message, only ciphertext,
its size and its time.

Security model:
  - Pairing needs a person at a terminal on both machines: `invite` and `join`
    refuse to run without a TTY, so an agent cannot pair a machine on its own,
    even when a message tells it to. Same for `allow` and `relay-server`.
  - The invite code carries a one-time 256-bit secret, valid 15 minutes. The two
    machines run an X25519 exchange authenticated with it, then both terminals
    show a 6-digit code that the two people compare before anything is saved.
  - Each pair gets its own root key. Every frame is sealed with
    ChaCha20-Poly1305, with a key and a relay topic per direction derived from
    it. A frame that was changed, replayed or sealed with another key is dropped.
  - A paired machine reaches only the local sessions its allow list names, plus
    the local sessions that wrote to it in the last 24 hours (so replies work).
    Wildcards never cover agy:new and agy:new-cli: those must be named exactly.
  - The gateway writes the sender as `remote:<peer>/<address>`: the peer name is
    the one you chose and is authenticated by the key, so a remote agent can
    never pass for a local one. Messages cannot hop on to a third machine.
  - Inbound messages are rate-limited per peer. Keys stay in
    ~/.agent-bridge/remote/peers/, readable by your user only.

Needs the `cryptography` package (pip install cryptography), for remote
messaging only: the rest of the bridge stays standard library only.
"""
import base64
import collections
import contextlib
import fnmatch
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import signal
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge  # noqa: E402  (bridge.py ships next to this file)

DEFAULT_RELAY = "https://ntfy.sh"
INVITE_SECONDS = 15 * 60
CONFIRM_SECONDS = 5 * 60
FRAME_TEXT_MAX = 4000  # ntfy.sh takes messages up to 4096 bytes
CHUNK = 2900  # plaintext bytes per frame: base64 of nonce + header + chunk + tag stays under FRAME_TEXT_MAX
MAX_PARTS = 64  # frames per payload: a 64 KiB body, even with every character escaped in JSON
PAD = 256  # payloads are padded to a multiple of this, so the relay learns less from sizes
MAX_BODY = 64 * 1024
MAX_AGE_SECONDS = 13 * 3600  # older frames are refused: a relay keeps messages 12 h
CONTACT_SECONDS = 24 * 3600  # a local session that wrote to a peer may get replies this long
INBOUND_PER_MINUTE = 20
ROSTER_CHECK_SECONDS = 60
ROSTER_MIN_SECONDS = 1800  # each roster costs relay messages: ntfy.sh allows 250 a day
ROSTER_MAX = 50
STREAM_TIMEOUT = 90  # ntfy sends a keepalive every 45 s
RELOAD_SECONDS = 5
BEAT_SECONDS = 10  # the gateway rewrites gateway.json this often
BEAT_STALE_SECONDS = 60  # older: the gateway is gone, whatever process now holds its pid
NOTICE_SECONDS = 60  # at most one notice a minute per peer: each one costs relay quota
OPEN_SEQS_MAX = 8  # payloads being reassembled per peer; older ones are dropped
LINE_MAX = 65536  # longest relay line read
# What a notice can say. The text is written here, never by the other machine.
NOTICES = {
    "refused": "not delivered: no such session there, or it does not accept messages from this machine",
    "rate": f"messages dropped: at most {INBOUND_PER_MINUTE} a minute are accepted",
    "unavailable": "not delivered: the session there could not take the message",
}
USER_AGENT = "diaulos-remote/1"
PEER_RE = bridge.PEER_NAME_RE
LOCAL_RE = bridge.ADDRESS_RE  # a sender address on the other machine: agy|claude|ext[:name]
FRAME = struct.Struct(">QHH")  # seq, part, parts
PAIR_KINDS = ("inviter", "joiner")


# ---------------------------------------------------------------- files

def _dir(*parts):
    path = bridge._path("remote", *parts)
    if not os.path.isdir(path):
        os.makedirs(path, mode=0o700, exist_ok=True)  # keys and state: this user only
    return path


def _pair_file(name):
    return os.path.join(_dir("peers"), name + ".json")


def _state_file(name, suffix=""):
    return os.path.join(_dir("state"), name + suffix + ".json")


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data, private=False):
    """Atomic write; `private` files (keys) are created readable by this user only."""
    tmp = f"{path}.{os.getpid()}.tmp"
    with contextlib.suppress(OSError):
        os.remove(tmp)  # a file left by a crash would keep its old mode
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if private else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
        f.flush()
        os.fsync(f.fileno())  # after a power cut, never an empty state that would replay 12 h of messages
    bridge._replace(tmp, path)


def pairs():
    """{name: pair record} of every paired machine."""
    found = {}
    for entry in sorted(os.listdir(_dir("peers"))):
        if entry.endswith(".json") and PEER_RE.match(entry[:-5]):
            record = _read_json(os.path.join(_dir("peers"), entry))
            if isinstance(record, dict) and record.get("root"):
                found[entry[:-5]] = record
    return found


def log(*args):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *args, flush=True)


# ---------------------------------------------------------------- crypto

def _crypto():
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError:
        sys.exit("remote messaging needs the cryptography package: python3 -m pip install cryptography")
    return dict(hashes=hashes, serialization=serialization, X25519PrivateKey=X25519PrivateKey,
                X25519PublicKey=X25519PublicKey, ChaCha20Poly1305=ChaCha20Poly1305, HKDF=HKDF)


def b64e(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hkdf(key, info, salt=None, length=32):
    c = _crypto()
    return c["HKDF"](algorithm=c["hashes"].SHA256(), length=length, salt=salt,
                     info=info.encode() if isinstance(info, str) else info).derive(key)


def _topic(key, info):
    return "dl" + base64.b32encode(hkdf(key, info)).decode("ascii").lower().rstrip("=")[:30]


def _mac(key, *parts):
    return b64e(hmac.new(key, b"|".join(p if isinstance(p, bytes) else p.encode() for p in parts),
                         hashlib.sha256).digest())


def _mac_ok(key, mac, *parts):
    return isinstance(mac, str) and hmac.compare_digest(_mac(key, *parts), mac)


class Channel:
    """The keys and topics of one pair, from this machine's side."""

    def __init__(self, record):
        root = b64d(record["root"])
        me = record["role"]
        other = PAIR_KINDS[1 - PAIR_KINDS.index(me)]
        self.relay = record["relay"]
        self.key_out, self.key_in = hkdf(root, f"key {me}"), hkdf(root, f"key {other}")
        self.topic_out, self.topic_in = _topic(root, f"topic {me}"), _topic(root, f"topic {other}")

    def seal(self, payload, seq):
        """Frames (relay message texts) carrying `payload`, a dict."""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        data += b" " * (-len(data) % PAD)  # JSON allows trailing whitespace
        chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)]
        if len(chunks) > MAX_PARTS:
            raise ValueError("payload too large")
        aead = _crypto()["ChaCha20Poly1305"](self.key_out)
        frames = []
        for part, chunk in enumerate(chunks):
            nonce = secrets.token_bytes(12)
            sealed = aead.encrypt(nonce, FRAME.pack(seq, part, len(chunks)) + chunk, self.topic_out.encode())
            frames.append("d1" + b64e(nonce + sealed))
        return frames

    def open(self, text):
        """(seq, part, parts, chunk) of a frame from the other machine, or None if it is not genuine."""
        if not isinstance(text, str) or not text.startswith("d1") or len(text) > FRAME_TEXT_MAX + 100:
            return None
        try:
            raw = b64d(text[2:])
            plain = _crypto()["ChaCha20Poly1305"](self.key_in).decrypt(raw[:12], raw[12:], self.topic_in.encode())
        except Exception:  # InvalidTag, bad base64: tampered, replayed from another pair, or noise
            return None
        if len(plain) < FRAME.size:
            return None
        seq, part, parts = FRAME.unpack(plain[:FRAME.size])
        if not 0 <= part < parts <= MAX_PARTS:
            return None
        return seq, part, parts, plain[FRAME.size:]


# ---------------------------------------------------------------- relay client

class RateLimited(Exception):
    pass


def _check_relay(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        sys.exit(f"relay must be an http(s) URL, not {url!r}")
    return url.rstrip("/")


class _Pace:
    """ntfy.sh allows 60 requests at once, then one every 5 s: every post of this process
    waits for a token, so a burst of long messages slows down instead of hitting 429."""

    def __init__(self, burst=55, every=5.0):
        self.burst, self.every, self.tokens, self.last = burst, every, float(burst), time.time()
        self.lock = threading.Lock()

    def take(self):
        with self.lock:
            now = time.time()
            self.tokens = min(self.burst, self.tokens + (now - self.last) / self.every)
            self.last = now
            wait = 0 if self.tokens >= 1 else (1 - self.tokens) * self.every
            self.tokens -= 1
        if wait:
            time.sleep(wait)


PACE = _Pace()


def publish(relay, topic, text):
    import http.client
    PACE.take()
    req = urllib.request.Request(f"{relay}/{topic}", data=text.encode("ascii"), method="POST",
                                 headers={"User-Agent": USER_AGENT, "Content-Type": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited(f"{relay} says too many requests") from e
        raise
    except http.client.HTTPException as e:  # IncompleteRead, BadStatusLine: a relay misbehaving
        raise OSError(f"{relay}: {e!r}") from e


def subscribe(relay, topics, since, stop=lambda: False):
    """Yield the relay's messages on `topics` ({"topic", "message", "time", "id"}), forever.

    Reconnects on errors. A keepalive also yields None, so the caller can stop
    or change topics at least every 45 seconds.
    """
    backoff = 2
    while not stop():
        url = f"{relay}/{','.join(sorted(topics))}/json?since={since or 'all'}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=STREAM_TIMEOUT) as resp:
                backoff = 2
                while True:
                    raw = resp.readline(LINE_MAX)
                    if not raw:
                        break
                    if not raw.endswith(b"\n"):  # longer than LINE_MAX: not ours, skip the rest of it
                        while raw and not raw.endswith(b"\n"):
                            raw = resp.readline(LINE_MAX)
                        continue
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(event, dict) or not isinstance(event.get("topic"), str):
                        yield None
                        continue
                    if event.get("event") == "message" and event.get("topic") in topics:
                        stamp = event.get("time")
                        if isinstance(stamp, int):
                            since = max(int(since) if str(since).isdigit() else 0, stamp)
                        yield event
                    else:
                        yield None
                    if stop():
                        return
        except Exception as e:  # noqa: BLE001  an untrusted relay must never kill the reader
            log(f"relay {relay}: {e!r}; retrying in {backoff} s")
            yield None
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ---------------------------------------------------------------- pairing

def _require_human(what):
    """Pairing and widening access are for people only: agents run commands without a terminal."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.exit(f"`{what}` needs a person at a terminal, by design: an agent must not pair machines "
                 "or open access on its own. Run it yourself in a terminal.")


def _ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def make_invite(relay):
    secret = secrets.token_bytes(32)
    code = "diaulos1." + b64e(json.dumps({"r": relay, "s": b64e(secret),
                                          "e": int(time.time()) + INVITE_SECONDS}).encode())
    return code, secret


def read_invite(code):
    try:
        data = json.loads(b64d(code.strip().split(".", 1)[1]))
        return _check_relay(data["r"]), b64d(data["s"]), int(data["e"])
    except (IndexError, KeyError, ValueError, TypeError):
        sys.exit("this is not a Diaulos invite code")


def _sas(root):
    number = int.from_bytes(hkdf(root, "sas", length=4), "big") % 1_000_000
    return f"{number // 1000:03d} {number % 1000:03d}"


def _exchange(role, relay, secret, deadline, since):
    """X25519 exchange on the pairing topic, authenticated with the invite secret: the root key."""
    c = _crypto()
    topic, mac_key = _topic(secret, "pair topic"), hkdf(secret, "pair mac")
    mine = c["X25519PrivateKey"].generate()
    raw = c["serialization"].Encoding.Raw
    my_pub = mine.public_key().public_bytes(raw, c["serialization"].PublicFormat.Raw)
    if role == "joiner":
        publish(relay, topic, json.dumps({"t": "join", "pub": b64e(my_pub), "mac": _mac(mac_key, "join", my_pub)}))
    for event in subscribe(relay, {topic}, since, stop=lambda: time.time() > deadline):
        if not event:
            continue
        try:
            msg = json.loads(event["message"])
            their = b64d(msg["pub"])
        except (ValueError, KeyError, TypeError):
            continue
        if role == "inviter" and msg.get("t") == "join" and _mac_ok(mac_key, msg.get("mac"), "join", their):
            publish(relay, topic, json.dumps({"t": "accept", "pub": b64e(my_pub),
                                              "mac": _mac(mac_key, "accept", my_pub, their)}))
            inviter_pub, joiner_pub = my_pub, their
        elif role == "joiner" and msg.get("t") == "accept" and _mac_ok(mac_key, msg.get("mac"), "accept", their, my_pub):
            inviter_pub, joiner_pub = their, my_pub
        else:
            continue  # our own message, or someone without the invite secret
        shared = mine.exchange(c["X25519PublicKey"].from_public_bytes(their))
        root = hkdf(shared, b"diaulos remote v1 root|" + inviter_pub + b"|" + joiner_pub, salt=secret)
        return root, topic
    sys.exit("the invite expired before the other machine answered")


def _confirm(role, relay, topic, root, ok, deadline, since):
    """Tell the other side whether its person confirmed the code; True once both did."""
    key = hkdf(root, "confirm")
    word = "ok" if ok else "no"
    publish(relay, topic, json.dumps({"t": word, "r": role, "mac": _mac(key, word, role)}))
    if not ok:
        return False
    other = PAIR_KINDS[1 - PAIR_KINDS.index(role)]
    for event in subscribe(relay, {topic}, since, stop=lambda: time.time() > deadline):
        if not event:
            continue
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            msg = json.loads(event["message"])
            if msg.get("r") == other and msg.get("t") in ("ok", "no") and _mac_ok(key, msg.get("mac"), msg["t"], other):
                return msg["t"] == "ok"
    return False


def pair(role, relay, secret, deadline, confirm, since=None):
    """Run the pairing protocol; return the root key once both people confirmed, else None.

    `confirm(code)` shows the 6-digit code and returns the person's answer.
    """
    # The pairing topic is new for each invite: read it whole, so no local clock is involved.
    since = since or "all"
    root, topic = _exchange(role, relay, secret, deadline, since)
    ok = bool(confirm(_sas(root)))
    return root if _confirm(role, relay, topic, root, ok, time.time() + CONFIRM_SECONDS, since) else None


def record(role, relay, root, allow=()):
    return {"v": 1, "role": role, "relay": relay, "root": b64e(root), "allow": list(allow),
            "paired_at": int(time.time()), "code": _sas(root)}


def _ask_allow(name):
    print(f"\nWhich of your sessions may {name} message first? Replies to sessions that wrote to {name} "
          "always get through. Examples: claude:review  agy:*  ext:ci. Empty: none (you can run "
          f"`remote allow {name} <pattern>` later). agy:new and agy:new-cli are only allowed by exact name.")
    return [p for p in _ask("Patterns, separated by spaces: ").split() if p]


def _save_pair(name, role, relay, root):
    """Save the pair at once, with nothing allowed: an interrupted prompt never leaves one side unpaired."""
    _write_json(_pair_file(name), record(role, relay, root), private=True)
    _write_json(_state_file(name), {"send_seq": 0, "recv_seq": 0, "since": 0, "contacts": {}})
    allow = _ask_allow(name)
    if allow:
        found = _read_json(_pair_file(name))
        found["allow"] = allow
        _write_json(_pair_file(name), found, private=True)
    print(f"\nPaired with {name}. Its sessions are reachable as remote:{name}/<address>.")
    print(f"Gateway running, pid {start()}. It stops with your session or a reboot: agents restart it,")
    print("and README › Other machines shows how to start it at login.")


def _check_name(name):
    if not PEER_RE.match(name or ""):
        sys.exit("peer name: lowercase letters, digits, '-' and '_', 32 at most, e.g. `laptop` or `alice`")
    if os.path.exists(_pair_file(name)):
        sys.exit(f"{name} is already paired; `remote unpair {name}` first")


def _ask_code(code):
    print(f"\nVerification code: {code}")
    print("Compare it with the code shown on the other machine, by voice or another channel you trust.")
    return _ask("Same code on both machines? [y/N] ").lower() in ("y", "yes", "o", "oui")


def _pair_or_exit(role, relay, secret, deadline):
    try:
        root = pair(role, relay, secret, deadline, _ask_code)
    except (OSError, RateLimited) as e:
        sys.exit(f"the relay {relay} is unreachable or refused the request ({e}). Nothing was saved.")
    if not root:
        sys.exit("Pairing cancelled: the codes were not confirmed on both machines. Nothing was saved.")
    return root


def cmd_invite(args):
    _require_human("remote invite")
    if not args:
        sys.exit("usage: bridge.py remote invite <name for the other machine> [--relay URL]")
    name, relay = args[0], DEFAULT_RELAY
    if args[1:2] == ["--relay"] and len(args) == 3:
        relay = args[2]
    elif args[1:]:
        sys.exit("usage: bridge.py remote invite <name> [--relay URL]")
    _check_name(name)
    _crypto()
    relay = _check_relay(relay)
    code, secret = make_invite(relay)
    print("Give this one-time code to the person at the other machine, over a channel you trust.")
    print("Never through an agent, an issue or a chat that agents read. It expires in 15 minutes.\n")
    print(f"  {code}\n")
    print("On the other machine: python3 ~/.agent-bridge/bridge.py remote join <their name for you>\n")
    print("Waiting for the other machine...")
    root = _pair_or_exit("inviter", relay, secret, time.time() + INVITE_SECONDS)
    _save_pair(name, "inviter", relay, root)


def cmd_join(args):
    _require_human("remote join")
    if len(args) != 1:
        sys.exit("usage: bridge.py remote join <name for the other machine>   (the code is asked for, hidden)")
    name = args[0]
    _check_name(name)
    _crypto()
    import getpass
    # Never on the command line: other processes could read it there, and shells keep history.
    code = getpass.getpass("Invite code (hidden): ")
    relay, secret, expires = read_invite(code)
    if time.time() > expires:
        sys.exit("this invite code has expired: ask for a new one")
    print(f"Joining through {relay}...")
    root = _pair_or_exit("joiner", relay, secret, expires)
    _save_pair(name, "joiner", relay, root)


def _get_pair(name):
    found = pairs().get(name)
    if not found:
        sys.exit(f"no paired machine named {name!r}")
    return found


def cmd_allow(args, add):
    if len(args) != 2:
        sys.exit(f"usage: bridge.py remote {'allow' if add else 'deny'} <peer> <pattern>")
    if add:
        _require_human("remote allow")  # narrowing access is always fine, widening is for people
    name, pattern = args
    found = _get_pair(name)
    allow = [p for p in found.get("allow", []) if p != pattern] + ([pattern] if add else [])
    found["allow"] = allow
    _write_json(_pair_file(name), found, private=True)
    print(f"{name} may message first: {' '.join(allow) or '(none)'}")


def cmd_unpair(args):
    if len(args) != 1:
        sys.exit("usage: bridge.py remote unpair <peer>")
    _get_pair(args[0])
    for path in (_pair_file(args[0]), _state_file(args[0]), _state_file(args[0], ".roster")):
        with contextlib.suppress(OSError):
            os.remove(path)
    print(f"Unpaired {args[0]}: its key is deleted. Ask the other side to run `remote unpair` too.")


# ---------------------------------------------------------------- gateway

def _gateway_file():
    return bridge._path("remote", "gateway.json")


def gateway_pid():
    """Pid of the running gateway, or None. A pid alone is not enough: after a crash or a
    reboot, another process may hold it. The gateway's heartbeat in gateway.json must be fresh."""
    info = _read_json(_gateway_file(), {}) or {}
    pid, beat = info.get("pid"), info.get("beat", 0)
    if not isinstance(beat, (int, float)) or time.time() - beat > BEAT_STALE_SECONDS or not bridge._alive(pid):
        return None
    if not bridge.WINDOWS:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if b"gateway" not in f.read():
                    return None
        except OSError:
            return None
    return pid


def _beat(pid):
    _write_json(_gateway_file(), {"pid": pid, "beat": time.time()})


def start():
    """Start the gateway in the background unless it runs; its pid."""
    if not pairs():
        sys.exit("no paired machine: a person pairs one first with `bridge.py remote invite` / `remote join`")
    _crypto()
    with bridge._locked(_gateway_file()):
        pid = gateway_pid()
        if pid:
            return pid
        pid = bridge._spawn_detached([sys.executable, "-u", bridge.SCRIPT, "remote", "gateway"], "remote-gateway.log")
        _beat(pid)
    return pid


def stop():
    """Stop the gateway and wait for it to exit; its pid, or None if it did not run."""
    pid = gateway_pid()
    if pid:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
        deadline = time.time() + 5
        while bridge._alive(pid) and time.time() < deadline:
            time.sleep(0.1)
        with contextlib.suppress(OSError):
            if (_read_json(_gateway_file(), {}) or {}).get("pid") == pid:
                os.remove(_gateway_file())  # Windows: TerminateProcess skips the gateway's own cleanup
    return pid


def allowed(record, state, address, target):
    """May this peer deliver to local `address` (asked as `target`)?

    A reply window only opens for the exact address that wrote to this peer: `target`
    must be that address itself, never a prefix, a title, an alias or `claude` resolving
    to some other session.
    """
    if address.startswith("remote:"):
        return False  # no hop to a third machine
    if target == address and time.time() - state.get("contacts", {}).get(address, 0) < CONTACT_SECONDS:
        return True
    for pattern in record.get("allow", []):
        if address in (bridge.NEW, bridge.NEW_CLI):
            if pattern == address:
                return True
        elif fnmatch.fnmatchcase(address, pattern):
            return True
    return False


def _new_cli_workspace(name):
    """A fresh, empty folder for each agy CLI conversation a peer starts: outside the bridge
    home and its keys, and never shared, so one conversation cannot plant rule files
    (GEMINI.md, .agents/) for the next."""
    import tempfile
    base = os.path.join(os.path.expanduser("~"), ".cache", "diaulos-remote", name)
    os.makedirs(base, mode=0o700, exist_ok=True)
    return tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=base)


class Gateway:
    """One process for every pair: local inboxes remote:<peer> out, relay frames in."""

    def __init__(self):
        self.records, self.channels, self.states = {}, {}, {}
        self.inbound = queue.Queue()
        self.streams = {}  # relay -> set of topics its thread reads
        self.lock = threading.Lock()
        self.parts = collections.defaultdict(dict)  # (peer, seq) -> {part: chunk}
        self.recent = collections.defaultdict(collections.deque)  # peer -> times of accepted messages
        self.backoff_until = 0
        self.roster_sent, self.roster_checked, self.roster_wanted = {}, 0, set()
        self.notice_sent = {}
        self.running = True
        self.taken_over = False
        self.progress = time.time()

    # -- pairs

    def reload(self):
        records = pairs()
        for name, rec in records.items():
            if rec != self.records.get(name):
                self.channels[name] = Channel(rec)
            self.states.setdefault(name, _read_json(_state_file(name), {}) or {})
        for name in set(self.records) - set(records):
            self.channels.pop(name, None)
            self.states.pop(name, None)
        self.records = records
        wanted = collections.defaultdict(set)
        for name in records:
            wanted[self.channels[name].relay].add(self.channels[name].topic_in)
        with self.lock:
            for relay, topics in wanted.items():
                if relay not in self.streams:
                    since = self._since_of(topics)
                    self.streams[relay] = topics
                    threading.Thread(target=self.read_relay, args=(relay, since), daemon=True).start()
                else:
                    self.streams[relay] = topics
            for relay in set(self.streams) - set(wanted):
                self.streams[relay] = set()

    def save_state(self, name):
        if name in self.states:
            contacts = self.states[name].get("contacts", {})
            for address in [a for a, t in contacts.items() if time.time() - t > CONTACT_SECONDS]:
                del contacts[address]
            _write_json(_state_file(name), self.states[name])

    def _since_of(self, topics):
        """Earliest `since` saved for the pairs reading `topics`: 0 (everything cached) if unknown."""
        values = [self.states.get(n, {}).get("since", 0) for n, c in list(self.channels.items()) if c.topic_in in topics]
        return min(values) if values else 0

    def read_relay(self, relay, since):
        with self.lock:
            topics = set(self.streams[relay])
        while self.running:
            changed = lambda: not self.running or self.streams.get(relay) != topics  # noqa: E731
            try:
                for event in subscribe(relay, topics, since, stop=changed):
                    if event:
                        if isinstance(event.get("time"), int):
                            since = max(since, event["time"])
                        self.inbound.put(event)
            except Exception as e:  # noqa: BLE001
                log(f"relay reader {relay}: {e!r}")
                time.sleep(5)
            with self.lock:
                new = set(self.streams.get(relay, ()))
                if not new:
                    self.streams.pop(relay, None)
                    return
            if new - topics:  # a new pair: read its topic from its own start, not from where the others are
                since = min(since, self._since_of(new - topics))
            topics = new

    # -- outbound

    def send_payload(self, name, payload):
        state, channel = self.states[name], self.channels[name]
        seq = max(time.time_ns(), state.get("send_seq", 0) + 1)
        state["send_seq"] = seq
        self.save_state(name)
        for frame in channel.seal(payload, seq):
            self.progress = time.time()  # a long paced payload is progress, not a hang
            try:
                publish(channel.relay, channel.topic_out, frame)
            except RateLimited:
                self.backoff_until = time.time() + 60
                raise

    def flush_outbox(self):
        if time.time() < self.backoff_until:
            return
        # One message per peer per loop turn, so incoming frames are read between long sends.
        # One claim at a time: a gateway stopped halfway never strands claimed messages in the archive.
        for name in list(self.records):
            claimed = bridge.claim(f"remote:{name}", limit=1)
            if not claimed:
                continue
            try:
                if not self.send_one(name, claimed[0][1]):
                    bridge.unclaim(f"remote:{name}", claimed)
                    return
            except BaseException:
                bridge.unclaim(f"remote:{name}", claimed)
                raise

    def send_one(self, name, msg):
        """Publish one claimed message; False to put it back and retry later."""
        payload = {"type": "msg", "id": msg["id"], "from": msg["from"], "to": msg.get("remote_to", ""),
                   "body": msg["body"], "ts": int(time.time())}
        if not LOCAL_RE.match(msg["from"]) or len(msg["body"].encode()) > MAX_BODY:
            log(f"dropped {msg['id']} to {name}: sender {msg['from']!r} or size not allowed")
            return True
        try:
            self.send_payload(name, payload)
        except ValueError:
            log(f"dropped {msg['id']} to {name}: too large once encoded")
            return True
        except (OSError, RateLimited) as e:
            log(f"relay refused {msg['id']} for {name}: {e!r}; retrying later")
            self.backoff_until = max(self.backoff_until, time.time() + 10)
            return False
        # A reply window opens only for a session that exists now (or the shared `claude` inbox):
        # a made-up or closed address in an inbox file opens nothing. Local senders are not
        # authenticated, though: any local process can still name a live session.
        if msg["from"] == "claude" or any(p["address"] == msg["from"] for p in bridge.peers(msg["from"].split(":")[0])):
            self.states[name].setdefault("contacts", {})[msg["from"]] = time.time()
        self.save_state(name)
        log(f"sent {msg['id']} from {msg['from']} to remote:{name}/{payload['to']}")
        return True

    def notice(self, name, to, code):
        """Tell a sender on the other machine that its message was not delivered: a fixed code,
        never local details. At most one a minute per peer, none while the relay pushes back."""
        if time.time() < self.backoff_until or time.time() - self.notice_sent.get(name, 0) < NOTICE_SECONDS:
            return
        self.notice_sent[name] = time.time()
        with contextlib.suppress(OSError, RateLimited, ValueError):
            self.send_payload(name, {"type": "notice", "to": to, "code": code, "ts": int(time.time())})

    # -- inbound

    def handle_frame(self, event):
        name = next((n for n, c in self.channels.items() if c.topic_in == event.get("topic")), None)
        if not name:
            return
        opened = self.channels[name].open(event.get("message"))
        if not opened:
            log(f"dropped a frame on {name}'s topic: not sealed with its key")
            return
        seq, part, parts, chunk = opened
        state = self.states[name]
        if seq <= state.get("recv_seq", 0):
            return  # already handled: a replay, or the relay resending after a reconnect
        if (name, seq) not in self.parts:
            open_seqs = sorted(k for k in self.parts if k[0] == name)
            for key in open_seqs[:max(0, len(open_seqs) - OPEN_SEQS_MAX + 1)]:
                del self.parts[key]  # never completed: lost frames, or a peer filling memory
        buffer = self.parts[(name, seq)]
        buffer[part] = chunk
        if len(buffer) < parts:
            return
        del self.parts[(name, seq)]
        for key in [k for k in self.parts if k[0] == name and k[1] < seq]:
            del self.parts[key]  # frames lost on the way: that payload never completes
        state["recv_seq"] = seq
        if isinstance(event.get("time"), int):
            state["since"] = max(state.get("since", 0), event["time"] - 1)
        self.save_state(name)
        try:
            payload = json.loads(b"".join(buffer[i] for i in range(parts)).decode("utf-8"))
        except (ValueError, KeyError):
            return
        ts = payload.get("ts") if isinstance(payload, dict) else None
        if not isinstance(ts, int) or time.time() - ts > MAX_AGE_SECONDS:
            log(f"dropped a payload from {name}: malformed or too old")
            return
        handler = {"msg": self.on_msg, "notice": self.on_notice, "roster": self.on_roster,
                   "roster?": self.on_roster_request}.get(payload.get("type"))
        if handler:
            try:
                handler(name, payload)
            except Exception as e:  # noqa: BLE001  a malformed payload must not stop the gateway
                log(f"dropped a payload from {name}: {e!r}")

    def on_msg(self, name, p):
        frm, target, body = p.get("from"), p.get("to"), p.get("body")
        if not (isinstance(frm, str) and LOCAL_RE.match(frm) and isinstance(target, str)
                and re.match(r"^(agy|claude|ext)([:@][^\s/\\]{1,200})?$", target) and isinstance(body, str)
                and len(body.encode()) <= MAX_BODY):
            log(f"dropped a malformed message from {name}")
            return
        times = self.recent[name]
        while times and time.time() - times[0] > 60:
            times.popleft()
        if len(times) >= INBOUND_PER_MINUTE:
            log(f"rate limit: dropped {p.get('id')} from {name}")
            self.notice(name, frm, "rate")
            return
        times.append(time.time())
        sender = f"remote:{name}/{frm}"
        # Unknown and refused look the same from there: the other machine learns nothing
        # about local sessions, their names or their folders. Details stay in the local log.
        try:
            address = bridge.resolve(target, sender)
        except SystemExit as e:
            log(f"refused {p.get('id')} from {sender} to {target!r}: {str(e).splitlines()[0]}")
            self.notice(name, frm, "refused")
            return
        if not allowed(self.records[name], self.states[name], address, target):
            log(f"refused {p.get('id')} from {sender} to {address}: not in the allow list")
            self.notice(name, frm, "refused")
            return
        workspace = _new_cli_workspace(name) if address == bridge.NEW_CLI else None
        try:
            msg = bridge.send(target, body, sender, workspace)
        except SystemExit as e:
            log(f"could not deliver {p.get('id')} from {sender} to {address}: {str(e).splitlines()[0]}")
            self.notice(name, frm, "unavailable")
            return
        log(f"delivered {p.get('id')} from {sender} to {msg['to']}")

    def on_notice(self, name, p):
        to, text = p.get("to"), NOTICES.get(p.get("code"))
        contacts = self.states[name].get("contacts", {})
        # Only to the exact local session that wrote to this peer, and only text written here:
        # a notice is never a way in, nor a way to put words in the gateway's mouth.
        if not (isinstance(to, str) and text and time.time() - contacts.get(to, 0) < CONTACT_SECONDS):
            return
        times = self.recent[name]  # notices count against the same rate limit as messages
        while times and time.time() - times[0] > 60:
            times.popleft()
        if len(times) >= INBOUND_PER_MINUTE:
            return
        times.append(time.time())
        with contextlib.suppress(SystemExit):
            if bridge.resolve(to, f"remote:{name}") == to:
                bridge.send(to, f"[Diaulos gateway, about your message to {name}] {text}", f"remote:{name}")

    def on_roster(self, name, p):
        agents = p.get("agents")
        if not isinstance(agents, list):
            return
        agents = [a for a in agents if isinstance(a, dict) and isinstance(a.get("address"), str)
                  and LOCAL_RE.match(a["address"])][:ROSTER_MAX]
        # Names end up in `list`, read by agents: no line breaks or quotes to fake entries with.
        clean = [{"address": a["address"], "name": re.sub(r"[^A-Za-z0-9 ._-]", "", str(a.get("name") or ""))[:80]}
                 for a in agents]
        _write_json(_state_file(name, ".roster"), {"at": int(time.time()), "agents": clean})

    def on_roster_request(self, name, p):
        self.roster_wanted.add(name)  # answered at the next check, still at most every ROSTER_MIN_SECONDS

    def roster_for(self, name):
        rec, found = self.records[name], []
        for peer in bridge.peers(days=bridge.RECENT_DAYS):
            address = peer["address"]
            if LOCAL_RE.match(address) and allowed(rec, {}, address, address):
                found.append({"address": address, "name": peer.get("name") or ""})
        for special in (bridge.NEW, bridge.NEW_CLI):
            if special in rec.get("allow", []):
                found.append({"address": special, "name": ""})
        return sorted(found, key=lambda a: a["address"])[:ROSTER_MAX]

    def send_rosters(self):
        if time.time() - self.roster_checked < ROSTER_CHECK_SECONDS or time.time() < self.backoff_until:
            return
        self.roster_checked = time.time()
        for name in list(self.records):
            roster = self.roster_for(name)
            last = self.roster_sent.get(name)
            if last and (time.time() - last[0] < ROSTER_MIN_SECONDS
                         or (last[1] == roster and name not in self.roster_wanted)):
                continue
            self.roster_wanted.discard(name)
            try:
                self.send_payload(name, {"type": "roster", "agents": roster, "ts": int(time.time())})
                self.roster_sent[name] = (time.time(), roster)
            except (OSError, RateLimited) as e:
                log(f"roster for {name} not sent: {e!r}")

    # -- loop

    @staticmethod
    def _step(fn, *args):
        """One step of the loop: an error is logged, never fatal (a paired machine must not be
        able to take the gateway down)."""
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001
            log(f"{fn.__name__}: {e!r}")

    def beat(self):
        """Heartbeat, from its own thread: a long publish in the main loop must not make the
        gateway look dead and start a second one. A main loop stuck for minutes stops the beat."""
        while self.running:
            info = _read_json(_gateway_file())
            # Leave only for another live gateway: a file that cannot be read for a
            # moment (Windows antivirus, a concurrent write) is no reason to go deaf.
            if isinstance(info, dict) and info.get("pid") not in (None, os.getpid()) and bridge._alive(info.get("pid")):
                self.taken_over = True
                return
            if time.time() - self.progress < 300:
                with contextlib.suppress(OSError):
                    _beat(os.getpid())
            time.sleep(BEAT_SECONDS)

    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        log(f"gateway pid {os.getpid()} up")
        self.progress = time.time()
        threading.Thread(target=self.beat, daemon=True).start()
        self._step(self.reload)
        for name in list(self.records):
            self._step(self.send_payload, name, {"type": "roster?", "ts": int(time.time())})
        last_reload = time.time()
        try:
            while True:
                self.progress = time.time()
                if self.taken_over:
                    log("another gateway took over; leaving")
                    return
                if time.time() - last_reload > RELOAD_SECONDS:
                    self._step(self.reload)
                    last_reload = time.time()
                    if not self.records:
                        log("no paired machine left; leaving")
                        return
                self._step(self.flush_outbox)
                try:
                    while True:
                        self._step(self.handle_frame, self.inbound.get(timeout=bridge.POLL_SECONDS))
                except queue.Empty:
                    pass
                self._step(self.send_rosters)
        finally:
            self.running = False
            with contextlib.suppress(OSError):
                if (_read_json(_gateway_file(), {}) or {}).get("pid") == os.getpid():
                    os.remove(_gateway_file())
            log("gateway stopped")


def run_gateway():
    with bridge._locked(_gateway_file()):
        other = gateway_pid()
        if other and other != os.getpid():
            sys.exit(f"the gateway already runs: pid {other}")
        _beat(os.getpid())
    Gateway().run()


def status():
    pid = gateway_pid()
    lines = [f"gateway: {'running, pid ' + str(pid) if pid else 'stopped'}"]
    for name, rec in pairs().items():
        state = _read_json(_state_file(name), {}) or {}
        roster = _read_json(_state_file(name, ".roster"), {}) or {}
        host = urllib.parse.urlparse(rec["relay"]).netloc
        lines.append(f"{name}: relay {host}, paired {time.strftime('%Y-%m-%d', time.localtime(rec['paired_at']))}, "
                     f"code {rec.get('code', '?')}")
        lines.append(f"  may message first: {' '.join(rec.get('allow', [])) or '(none)'}")
        reachable = " ".join(f"remote:{name}/{a['address']}" for a in roster.get("agents", []))
        lines.append(f"  reachable there: {reachable or '(none announced)'}")
        recent = [a for a, t in state.get("contacts", {}).items() if time.time() - t < CONTACT_SECONDS]
        if recent:
            lines.append(f"  replies allowed to: {' '.join(sorted(recent))}")
    if len(lines) == 1:
        lines.append("no paired machine")
    return "\n".join(lines)


# ---------------------------------------------------------------- self-hosted relay

class Relay:
    """In-memory relay with the subset of the ntfy API the gateway uses:
    POST /<topic>, GET /<topic>[,<topic>...]/json?since=<all|unix time|id|10m>[&poll=1].

    Exposed to the internet, so every resource is bounded: bytes kept, subscribers per
    client and in all, requests per client (posts and subscriptions share one bucket),
    and slow clients (socket timeout).
    """

    TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
    KEEP_SECONDS = 12 * 3600
    KEEP_BYTES = 64 * 1024 * 1024
    KEEP_PER_TOPIC = 500
    KEEPALIVE = 25
    SUBSCRIBERS_MAX = 500
    SUBSCRIBERS_PER_CLIENT = 8
    CLIENTS_MAX = 10000
    TIMEOUT = 30  # a whole request (line, headers, body) must arrive within this
    CONNECTIONS_MAX = 1000
    CONNECTIONS_PER_ADDRESS = 32

    def __init__(self, trusted_proxy=None):
        self.lock = threading.Lock()
        self.topics = collections.defaultdict(collections.deque)  # topic -> messages, oldest first
        self.order = collections.deque()  # (n, topic) of every message kept, oldest first
        self.bytes = 0
        self.count = 0  # messages kept: `order` also holds entries of messages already dropped
        self.next_n = 0
        self.waiters = collections.defaultdict(set)  # topic -> Events of its subscribers
        self.buckets = collections.OrderedDict()
        self.subs = collections.Counter()  # client -> open subscriptions
        self.trusted_proxy = self._ip(trusted_proxy) if trusted_proxy else None
        self.pending = {}  # socket -> time it connected, until its request is complete
        self.connections = collections.Counter()  # raw peer address -> open connections
        self.peer_of = {}  # socket -> raw peer address, counted in `connections`

    @staticmethod
    def _ip(address):
        """`address` as an ipaddress object, IPv4 for an IPv4-mapped IPv6 one; None if not an IP."""
        import ipaddress
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return None
        return ip.ipv4_mapped if ip.version == 6 and ip.ipv4_mapped else ip

    def client(self, address, forwarded):
        """Who to count a request against: the /64 for IPv6, and the address the trusted
        proxy forwarded when the request comes from it."""
        import ipaddress
        if self.trusted_proxy and self._ip(address) == self.trusted_proxy and forwarded:
            address = forwarded.split(",")[-1].strip()
        ip = self._ip(address)
        if ip is None:
            return address
        if ip.version == 6:
            return str(ipaddress.ip_network(f"{ip}/64", strict=False))
        return str(ip)

    def allow(self, client):
        """Token bucket per client: 60 at once, one more every 2 s."""
        with self.lock:
            tokens, last = self.buckets.pop(client, (60.0, time.time()))
            tokens = min(60.0, tokens + (time.time() - last) / 2)
            while len(self.buckets) >= self.CLIENTS_MAX:
                self.buckets.popitem(last=False)  # forget the least recently seen client
            self.buckets[client] = (tokens - 1 if tokens >= 1 else tokens, time.time())
        return tokens >= 1

    def _drop_oldest(self):
        n, topic = self.order.popleft()
        queue_ = self.topics[topic]
        if queue_ and queue_[0]["n"] == n:
            self.bytes -= len(queue_.popleft()["message"])
            self.count -= 1
        if not queue_:
            del self.topics[topic]

    def post(self, topic, text):
        with self.lock:
            msg = {"id": secrets.token_urlsafe(9), "time": int(time.time()), "event": "message",
                   "topic": topic, "message": text, "n": self.next_n}
            self.next_n += 1
            self.topics[topic].append(msg)
            self.order.append((msg["n"], topic))
            self.bytes += len(text)
            self.count += 1
            if len(self.topics[topic]) > self.KEEP_PER_TOPIC:
                self.bytes -= len(self.topics[topic].popleft()["message"])  # its entry in order goes stale
                self.count -= 1
            cutoff = time.time() - self.KEEP_SECONDS
            limit = 4 * self.KEEP_BYTES // 4096
            while self.order and (self.bytes > self.KEEP_BYTES or self.count > limit or self._oldest_time() < cutoff):
                self._drop_oldest()
            while len(self.order) > 4 * limit:  # stale entries only: never costs another topic a message
                n, t = self.order[0]
                q = self.topics.get(t)
                if q and q[0]["n"] == n:
                    break
                self.order.popleft()
            for event in self.waiters.get(topic, ()):
                event.set()
        return {k: v for k, v in msg.items() if k != "n"}

    def _oldest_time(self):
        n, topic = self.order[0]
        queue_ = self.topics.get(topic)
        if not queue_ or queue_[0]["n"] != n:
            return 0  # a stale entry: drop it
        return queue_[0]["time"]

    def since(self, topics, n):
        with self.lock:
            return sorted((m for t in topics for m in self.topics.get(t, ()) if m["n"] >= n), key=lambda m: m["n"])

    def start_of(self, topics, since):
        """Sequence number from which a subscriber starts."""
        if since in ("", None):
            return self.next_n
        if since == "all":
            return 0
        m = re.match(r"^(\d+)([smhd])$", since)
        kept = self.since(topics, 0)
        if since.isdigit() or m:
            t = int(since) if since.isdigit() else time.time() - int(m[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m[2]]
            return next((x["n"] for x in kept if x["time"] >= t), self.next_n)
        return next((x["n"] + 1 for x in kept if x["id"] == since), 0)

    def subscribe(self, client, topics):
        """An Event set on new messages for `topics`, or None when limits are reached."""
        with self.lock:
            if self.subs[client] >= self.SUBSCRIBERS_PER_CLIENT or sum(self.subs.values()) >= self.SUBSCRIBERS_MAX:
                return None
            self.subs[client] += 1
            event = threading.Event()
            for t in topics:
                self.waiters[t].add(event)
            return event

    def unsubscribe(self, client, topics, event):
        with self.lock:
            self.subs[client] -= 1
            if self.subs[client] <= 0:
                del self.subs[client]
            for t in topics:
                self.waiters[t].discard(event)
                if not self.waiters[t]:
                    del self.waiters[t]

    def handler(self):
        from http.server import BaseHTTPRequestHandler

        relay = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "diaulos-relay/1"
            timeout = relay.TIMEOUT  # a client that sends its request slowly is dropped

            def log_message(self, *args):
                pass  # no access log: who talks to whom stays out of files

            def who(self):
                return relay.client(self.client_address[0], self.headers.get("X-Forwarded-For"))

            def reply(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                topic = urllib.parse.urlparse(self.path).path.strip("/")
                try:
                    length = int(self.headers.get("Content-Length") or "")
                except ValueError:
                    return self.reply(411, {"error": "Content-Length required"})
                if not relay.TOPIC.match(topic):
                    return self.reply(404, {"error": "bad topic"})
                if not 0 <= length <= 4096:
                    return self.reply(413, {"error": "message too large"})
                if not relay.allow(self.who()):
                    return self.reply(429, {"error": "too many requests"})
                data = self.rfile.read(length)
                relay.request_complete(self.connection)
                if len(data) < length:
                    return  # the client left before sending its body
                self.reply(200, relay.post(topic, data.decode("utf-8", "replace")))

            do_PUT = do_POST

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                path = url.path.strip("/")
                if path in ("", "v1/health"):
                    return self.reply(200, {"healthy": True})
                if not path.endswith("/json"):
                    return self.reply(404, {"error": "not found"})
                topics = set(path[:-5].split(","))
                if not topics or len(topics) > 50 or not all(relay.TOPIC.match(t) for t in topics):
                    return self.reply(404, {"error": "bad topic"})
                client = self.who()
                if not relay.allow(client):
                    return self.reply(429, {"error": "too many requests"})
                query = urllib.parse.parse_qs(url.query)
                poll = query.get("poll", [""])[0] in ("1", "true", "yes")
                n = relay.start_of(topics, query.get("since", ["all" if poll else ""])[0])
                relay.request_complete(self.connection)
                event = relay.subscribe(client, topics)
                if event is None:
                    return self.reply(429, {"error": "too many subscriptions"})
                try:
                    self.stream(topics, n, poll, event)
                except OSError:
                    pass  # the subscriber left
                finally:
                    relay.unsubscribe(client, topics, event)

            def stream(self, topics, n, poll, event):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("X-Accel-Buffering", "no")  # nginx: pass each line on at once
                self.end_headers()
                label = ",".join(sorted(topics))
                self.line({"id": secrets.token_urlsafe(9), "time": int(time.time()), "event": "open", "topic": label})
                while True:
                    event.clear()
                    batch = relay.since(topics, n)
                    if not batch and not poll:
                        event.wait(relay.KEEPALIVE)
                        batch = relay.since(topics, n)
                    for m in batch:
                        self.line({k: v for k, v in m.items() if k != "n"})
                        n = m["n"] + 1
                    if poll:
                        return
                    if not batch:
                        self.line({"id": secrets.token_urlsafe(9), "time": int(time.time()),
                                   "event": "keepalive", "topic": label})

            def line(self, obj):
                self.wfile.write(json.dumps(obj).encode() + b"\n")
                self.wfile.flush()

        return Handler

    def request_complete(self, sock):
        with self.lock:
            self.pending.pop(sock, None)

    def _watchdog(self):
        """Close connections whose request is not complete after TIMEOUT: a client sending one
        byte at a time would otherwise hold a thread forever, before any limit applies."""
        import socket
        while True:
            time.sleep(2)
            with self.lock:
                late = [s for s, t in self.pending.items() if time.time() - t > self.TIMEOUT]
                for s in late:
                    del self.pending[s]
            for s in late:
                with contextlib.suppress(OSError):
                    s.shutdown(socket.SHUT_RDWR)

    def serve(self, host, port):
        from http.server import ThreadingHTTPServer
        relay = self

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            # IPv6 when the host is an IPv6 address
            address_family = __import__("socket").AF_INET6 if ":" in host else __import__("socket").AF_INET

            def verify_request(self, request, client_address):
                with relay.lock:
                    if sum(relay.connections.values()) >= relay.CONNECTIONS_MAX \
                            or relay.connections[client_address[0]] >= relay.CONNECTIONS_PER_ADDRESS:
                        return False
                    relay.connections[client_address[0]] += 1
                    relay.peer_of[request] = client_address[0]
                    relay.pending[request] = time.time()
                return True

            def shutdown_request(self, request):
                with relay.lock:
                    relay.pending.pop(request, None)
                    address = relay.peer_of.pop(request, None)
                    if address is not None:
                        relay.connections[address] -= 1
                        if relay.connections[address] <= 0:
                            del relay.connections[address]
                super().shutdown_request(request)

        server = Server((host, port), self.handler())
        threading.Thread(target=self._watchdog, daemon=True).start()
        return server


def cmd_relay_server(args):
    _require_human("remote relay-server")
    host, port, proxy = None, 8787, None
    while args:
        if args[0] == "--host" and len(args) > 1:
            host, args = args[1], args[2:]
        elif args[0] == "--port" and len(args) > 1 and args[1].isdigit():
            port, args = int(args[1]), args[2:]
        elif args[0] == "--trusted-proxy" and len(args) > 1:
            proxy, args = args[1], args[2:]
        else:
            sys.exit("usage: bridge.py remote relay-server [--host H] [--port P] [--trusted-proxy ADDRESS]")
    host = host or ("127.0.0.1" if proxy else "0.0.0.0")  # behind a proxy, only the proxy talks to it
    server = Relay(proxy).serve(host, port)
    print(f"Relay listening on http://{host}:{port} (Ctrl-C to stop).")
    print("It only ever sees ciphertext. Put it behind an HTTPS reverse proxy so that, on the way,")
    print("nobody sees which topics talk when. With nginx: proxy_buffering off; proxy_read_timeout 120s;")
    print("and start the relay with --trusted-proxy 127.0.0.1, so that limits count each client, not the proxy.")
    print("Pair with: bridge.py remote invite <name> --relay https://<your host>")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


# ---------------------------------------------------------------- cli

USAGE = """\
bridge.py remote <command>
  For people, in a terminal:
    invite <name> [--relay URL]   pair with another machine: prints a one-time code, then waits
    join <name>                   pair using the code the other machine printed (asked for, hidden)
    allow <name> <pattern>        let <name> message local sessions matching <pattern> first
    relay-server [--host H] [--port P] [--trusted-proxy ADDRESS]   host your own relay instead of ntfy.sh
  For people and agents:
    start | stop | status         the gateway that carries messages to and from paired machines
    deny <name> <pattern>         remove a pattern from the allow list
    unpair <name>                 forget a paired machine and its key
Address a session on a paired machine as remote:<name>/<address>, e.g. remote:laptop/claude:review."""


def main(args):
    cmd, rest = (args[0], args[1:]) if args else ("", [])
    if cmd == "invite":
        cmd_invite(rest)
    elif cmd == "join":
        cmd_join(rest)
    elif cmd in ("allow", "deny"):
        cmd_allow(rest, cmd == "allow")
    elif cmd == "unpair":
        cmd_unpair(rest)
    elif cmd == "start" and not rest:
        print(f"gateway running, pid {start()}")
    elif cmd == "stop" and not rest:
        pid = stop()
        print(f"gateway pid {pid} stopped" if pid else "the gateway was not running")
    elif cmd == "status" and not rest:
        print(status())
    elif cmd == "gateway" and not rest:
        run_gateway()
    elif cmd == "relay-server":
        cmd_relay_server(rest)
    else:
        sys.exit(USAGE)
