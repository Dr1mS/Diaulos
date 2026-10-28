"""Stand-in for the language server of Antigravity Desktop, for the tests. Linux only.

It renames itself `language_server`, so that the bridge takes it for the one
of a Desktop window, and keeps `--csrf_token <token>` and `--app_data_dir`
on its command line, as Desktop does. It serves the methods the bridge calls
and writes one JSON line per call to the log. POST /__run runs a command as
its child: hooks and `ensure-relay` then find it among their ancestors, as
they find the real server.

Usage: fake_ls.py --log FILE --port-file FILE --loaded FILE --csrf_token TOKEN
                  [--noconfig ID,ID] [--app_data_dir DIR]
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ctypes.CDLL(None).prctl(15, b"language_server", 0, 0, 0)  # PR_SET_NAME: /proc/<pid>/comm


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


TOKEN = arg("--csrf_token")
LOG = arg("--log")
LOADED = arg("--loaded")  # JSON file: the trajectorySummaries that GetAllCascadeTrajectories answers
NO_CONFIG = set(filter(None, (arg("--noconfig") or "").split(",")))  # conversations with no prompt yet
CONFIG = {"plannerConfig": {"planModel": "MODEL_FAKE"}}
started = set()  # conversations made by StartCascade: no prompt, so no config
lock = threading.Lock()


def log(entry):
    with lock, open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def project_folder(project):
    """First folder of a Desktop project, from HOME/.gemini/config/projects/<id>.json."""
    path = os.path.expanduser(f"~/.gemini/config/projects/{project}.json")
    try:
        with open(path, encoding="utf-8") as f:
            resources = json.load(f)["projectResources"]["resources"]
        return next(v["folderUri"] for r in resources for v in r.values() if "folderUri" in v)
    except (OSError, ValueError, KeyError, StopIteration):
        return None


class Api(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, status, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.answer(200 if self.path == "/healthz" else 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/__run":
            return self.run(body)
        if self.path == "/__exit":
            self.answer(200, {})
            threading.Thread(target=self.server.shutdown).start()
            return
        if self.headers.get("x-codeium-csrf-token") != TOKEN:
            return self.answer(401, {"code": "unauthenticated"})
        method = self.path.rsplit("/", 1)[-1]
        if method == "GetAllCascadeTrajectories":
            try:
                with open(LOADED, encoding="utf-8") as f:
                    summaries = json.load(f)
            except (OSError, ValueError):
                summaries = {}
            return self.answer(200, {"trajectorySummaries": summaries})
        if method == "GetCascadeTrajectory":
            cascade = body.get("cascadeId", "")
            bare = cascade in started or cascade in NO_CONFIG
            steps = [] if bare else [{"userInput": {"userConfig": CONFIG}}]
            return self.answer(200, {"trajectory": {"steps": steps}})
        if method == "StartCascade":
            log({"method": method, "body": body})
            if body.get("source") != "CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT":
                return self.answer(400, {"message": "CortexTrajectorySource is unspecified"})
            if body.get("projectEnvConfig") and body.get("workspaceUris"):
                return self.answer(400, {"message": "cannot specify both workspace URIs and project environment config"})
            cascade = str(uuid.uuid4())
            started.add(cascade)
            out = {"cascadeId": cascade}
            folder = project_folder((body.get("projectEnvConfig") or {}).get("projectId"))
            if folder:
                out["projectEnvInfo"] = {"workspaceUris": [folder]}
            return self.answer(200, out)
        if method == "UpdateConversationAnnotations":
            log({"method": method, "body": body})
            return self.answer(200, {})
        if method == "SendUserCascadeMessage":
            log({"method": method, "cascadeId": body.get("cascadeId"), "config": "cascadeConfig" in body,
                 "text": body["items"][0]["text"]})
            return self.answer(200, {})
        self.answer(404, {"message": f"unknown method {method}"})

    def run(self, body):
        """Runs a command as a child of this process; answers {code, stdout, stderr}."""
        env = dict(os.environ, **body.get("env", {}))
        proc = subprocess.run(body["argv"], input=body.get("stdin", ""), env=env, cwd=body.get("cwd"),
                              capture_output=True, text=True, timeout=60)
        self.answer(200, {"code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})


def main():
    api = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    decoy = ThreadingHTTPServer(("127.0.0.1", 0), type("Decoy", (Api,), {"do_GET": lambda self: self.answer(404)}))
    threading.Thread(target=decoy.serve_forever, daemon=True).start()
    with open(arg("--port-file") + ".tmp", "w") as f:
        f.write(str(api.server_address[1]))
    os.replace(arg("--port-file") + ".tmp", arg("--port-file"))
    api.serve_forever()


if __name__ == "__main__":
    main()
