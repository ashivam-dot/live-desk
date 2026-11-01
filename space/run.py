"""Entry point on a Hugging Face Space: one channel's stream, plus the small web page a Space must serve.

The channel comes from the CHANNEL variable and its stream key from the STREAM_KEY secret, both set in the Space's
settings. The page shows the stream's status (never the key); visits to it also keep a free Space awake.
"""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

CHANNEL = os.environ.get("CHANNEL", "orbitwire")
HOME = Path(os.environ.get("LIVE_DESK_HOME", "/data" if Path("/data").is_dir() else "/tmp/live-desk"))
PORT = int(os.environ.get("PORT", "7860"))
started = time.time()
restarts = 0


class Status(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            status = json.loads((HOME / CHANNEL / "status.json").read_text())
        except (OSError, ValueError):
            status = {}
        body = json.dumps({"channel": CHANNEL, "up_s": int(time.time() - started), "restarts": restarts,
                           "key_set": bool(os.environ.get("STREAM_KEY", "").strip()), **status},
                          ensure_ascii=False, indent=1).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def stream_forever() -> None:
    global restarts
    while True:
        code = subprocess.call([sys.executable, "-m", "livedesk.air", CHANNEL, "--home", str(HOME),
                                "--key-env", "STREAM_KEY", "--ingest", "rtmps"], cwd=Path(__file__).parents[1])
        restarts += 1
        print(f"stream exited with {code}; restarting in 10 s", flush=True)
        time.sleep(10)


if __name__ == "__main__":
    HOME.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=stream_forever, daemon=True).start()
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Status).serve_forever()
