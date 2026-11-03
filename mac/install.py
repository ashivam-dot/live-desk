"""Install, update or remove the launchd agents that run both streams on this Mac.

    python3 mac/install.py          install (or update to the latest code) and start both
    python3 mac/install.py remove   stop and remove both (keys and the running copy stay)

macOS keeps background services out of the Desktop, so the running copy lives in
~/Library/Application Support/live-desk, cloned from GitHub; stream keys sit in its private/ folder.
"""
import plistlib
import subprocess
import sys
from pathlib import Path

APP = Path.home() / "Library/Application Support/live-desk"
REPO = APP / "live-desk"
AGENTS = Path.home() / "Library/LaunchAgents"
GUI = f"gui/{subprocess.getoutput('id -u')}"


def run(*cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


remove = sys.argv[1:] == ["remove"]
if not remove:
    APP.mkdir(parents=True, exist_ok=True)
    if (REPO / ".git").exists():
        run("git", "-C", str(REPO), "pull", "--ff-only", "-q")
    else:
        run("git", "clone", "-q", "https://github.com/ashivam-dot/live-desk", str(REPO))
    if not (REPO / ".venv").exists():
        run("uv", "venv", "-q", "--python", "3.12", str(REPO / ".venv"))
    # the venv itself, not its python: that is a symlink uv would follow out to Homebrew's interpreter
    run("uv", "pip", "install", "-q", "--python", str(REPO / ".venv"), "-r", str(REPO / "requirements.txt"))
    (APP / "logs").mkdir(exist_ok=True)

for channel in ("orbitwire", "palpal"):
    label = f"com.ashivam-dot.live-desk.{channel}"
    plist = AGENTS / f"{label}.plist"
    subprocess.run(["launchctl", "bootout", GUI, str(plist)], capture_output=True)
    if remove:
        plist.unlink(missing_ok=True)
        print("removed", label)
        continue
    plist.write_bytes(plistlib.dumps({
        "Label": label,
        "ProgramArguments": [str(REPO / ".venv/bin/python"), str(REPO / "mac/host.py"), channel],
        "WorkingDirectory": str(REPO),
        "EnvironmentVariables": {"DYLD_LIBRARY_PATH": "/opt/homebrew/lib",
                                 "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 20,
        "ProcessType": "Interactive",
        "StandardOutPath": str(APP / "logs" / f"{channel}.log"),
        "StandardErrorPath": str(APP / "logs" / f"{channel}.log"),
    }))
    run("launchctl", "bootstrap", GUI, str(plist))
    print("started", label)
