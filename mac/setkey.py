"""Save one channel's YouTube stream key for the Mac host. The key is typed hidden and never printed.

    python3 mac/setkey.py orbitwire
    python3 mac/setkey.py palpal
"""
import getpass
import os
import sys
from pathlib import Path

PRIVATE = Path.home() / "Library/Application Support/live-desk/private"
CHANNELS = ("orbitwire", "palpal")

channel = sys.argv[1] if len(sys.argv) > 1 else ""
if channel not in CHANNELS:
    sys.exit(f"usage: python3 mac/setkey.py {'|'.join(CHANNELS)}")
key = getpass.getpass(f"Paste the {channel} stream key (it will not show) and press Enter: ").strip()
if len(key) < 10 or " " in key:
    sys.exit("That does not look like a stream key; nothing was saved.")
PRIVATE.mkdir(mode=0o700, exist_ok=True)
os.chmod(PRIVATE, 0o700)
path = PRIVATE / f"{channel}.key"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(key + "\n")
os.chmod(path, 0o600)
print(f"Saved. The {channel} stream starts within a few minutes.")
