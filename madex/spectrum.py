"""Spectre de la voix en 24 bandes (graves -> aigus), pour faire danser le cercle de l'interface."""
from __future__ import annotations

import numpy as np

BANDS = 24
_EDGES: dict[tuple[int, int], np.ndarray] = {}


def bands(block: np.ndarray, rate: int) -> list[float]:
    """24 valeurs entre 0 et 1, bandes espacées logarithmiquement de 90 Hz à 7 kHz."""
    n = len(block)
    if n < 64:
        return [0.0] * BANDS
    key = (n, rate)
    if key not in _EDGES:
        freqs = np.fft.rfftfreq(n, 1 / rate)
        edges = np.geomspace(90, min(7000, rate / 2 - 1), BANDS + 1)
        _EDGES[key] = np.searchsorted(freqs, edges)
    idx = _EDGES[key]
    x = block.astype(np.float32) * np.hanning(n)
    mag = np.abs(np.fft.rfft(x)) / (n * 32768 / 4)
    out = []
    for i in range(BANDS):
        a, b = idx[i], max(idx[i + 1], idx[i] + 1)
        v = float(mag[a:b].mean()) if b <= len(mag) else 0.0
        db = 20 * np.log10(v + 1e-6)                       # -120 .. 0 dB
        out.append(round(float(min(1.0, max(0.0, (db + 62) / 50))), 2))
    return out
