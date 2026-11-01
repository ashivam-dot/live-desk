"""The bed under live updates, made in code (no licensed music): a soft chord pad with a pulse, a seamless loop."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from .channels import Channel

RATE = 48000
LOOP_S = 32
# Chords as semitones above A2 (110 Hz), four bars each. Orbitwire: calm, wide, minor; Pal Pal: brighter, a news pulse.
PROGRESSIONS = {"orbitwire": [(0, 3, 7, 12), (-4, 0, 3, 8), (-2, 2, 5, 10), (-5, -1, 2, 7)],
                "palpal": [(0, 4, 7, 12), (5, 9, 12, 17), (-3, 0, 4, 9), (7, 11, 14, 19)]}
BPM = {"orbitwire": 84, "palpal": 96}


def _note(semitone: float, t: np.ndarray) -> np.ndarray:
    f = 110 * 2 ** (semitone / 12)
    return np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t + 0.5) + 0.12 * np.sin(6 * np.pi * f * t)


def render(key: str) -> np.ndarray:
    n = RATE * LOOP_S
    t = np.arange(n) / RATE
    chords = PROGRESSIONS[key]
    span = n // len(chords)
    pad = np.zeros(n)
    for i, chord in enumerate(chords):
        seg = slice(i * span, (i + 1) * span)
        tt = t[seg]
        env = np.minimum(1, np.minimum((tt - tt[0]) / 1.2, (tt[-1] - tt) / 1.2 + 0.15))
        pad[seg] = sum(_note(s, tt) for s in chord) * env
    beat = 60 / BPM[key]
    phase = (t % beat) / beat
    pulse = np.exp(-phase * 9) * np.sin(2 * np.pi * 55 * t) * (0.9 if key == "palpal" else 0.5)
    tick = (np.exp(-((t % (beat / 2)) / (beat / 2)) * 60) * np.random.default_rng(7).normal(0, 1, n)
            * (0.08 if key == "palpal" else 0.0))
    mono = pad / 4 + pulse * 0.35 + tick
    left = mono * (1 + 0.15 * np.sin(2 * np.pi * t / LOOP_S))
    right = mono * (1 - 0.15 * np.sin(2 * np.pi * t / LOOP_S))
    stereo = np.stack([left, right], axis=1)
    return stereo / np.abs(stereo).max() * 10 ** (-16 / 20)


def bed(ch: Channel, home: Path) -> Path:
    path = home / f"bed-{ch.key}.wav"
    if path.exists():
        return path
    home.mkdir(parents=True, exist_ok=True)
    data = (render(ch.key) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())
    return path
