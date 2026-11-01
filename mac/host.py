"""Run one channel's stream on this Mac (started by launchd, which restarts it whenever it exits).

Holds a keep-awake lease while the stream runs, so the Mac does not sleep mid-broadcast; the lease lapses on its own
within the hour if this process dies.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TASK = Path.home() / "Library/Application Support/live-desk"
KEEP_AWAKE = Path.home() / ".cursor/hooks/keep_awake.py"
channel = sys.argv[1]


def keep_awake() -> None:
    while True:
        if KEEP_AWAKE.exists():
            subprocess.run(["/usr/bin/python3", str(KEEP_AWAKE), "hold", f"live-desk-{channel}", "1"],
                           capture_output=True)
        time.sleep(1500)


key = TASK / "private" / f"{channel}.key"
if not key.exists():
    print(f"no stream key for {channel} yet (run: python3 mac/setkey.py {channel})", flush=True)
    time.sleep(300)
    sys.exit(1)
threading.Thread(target=keep_awake, daemon=True).start()
code = subprocess.call([sys.executable, "-m", "livedesk.air", channel, "--home", str(TASK / "state"),
                        "--key-file", str(key), "--ingest", "rtmps"], cwd=REPO)
sys.exit(code)
