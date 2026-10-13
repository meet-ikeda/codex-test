"""Room 06's gyri: a labyrinth grown by reaction-diffusion (Gray-Scott, the "maze" setting), like brain coral.

Writes src/exobrain/web/gyri.png, 500 x 360 grey (one cell per 2 stencil pixels of the 1000 x 720 brain profile in
intro.js). The page cuts it to the brain's shape. Run once: uv run python scripts/make_gyri.py
Stripes lean slightly upright (diffusion a little stronger along y), as the frontal and parietal gyri do.
"""
import struct
import zlib
from pathlib import Path

import numpy as np

W, H = 500, 360
rng = np.random.default_rng(5)
u = np.ones((H, W))
v = np.zeros((H, W))
for _ in range(220):  # scattered seeds
    y, x = rng.integers(4, H - 4), rng.integers(4, W - 4)
    v[y - 3:y + 3, x - 3:x + 3] = 0.5 + 0.1 * rng.random()
    u[y - 3:y + 3, x - 3:x + 3] = 0.25
Du, Dv, f, k = 0.16, 0.08, 0.029, 0.057
ax, ay = 0.85, 1.15  # anisotropy: the maze leans upright


def lap(a):
    p = np.pad(a, 1, mode="edge")
    return (ax * (p[1:-1, :-2] + p[1:-1, 2:]) + ay * (p[:-2, 1:-1] + p[2:, 1:-1]) - 2 * (ax + ay) * a)


for i in range(12000):
    uvv = u * v * v
    u += Du * lap(u) - uvv + f * (1 - u)
    v += Dv * lap(v) + uvv - (f + k) * v
img = np.clip(v / max(v.max(), 1e-6) * 255, 0, 255).astype(np.uint8)
raw = b"".join(b"\x00" + img[y].tobytes() for y in range(H))
png = b"\x89PNG\r\n\x1a\n"
for tag, data in ((b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 0, 0, 0, 0)), (b"IDAT", zlib.compress(raw, 9)), (b"IEND", b"")):
    png += struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
out = Path(__file__).parent.parent / "src" / "exobrain" / "web" / "gyri.png"
out.write_bytes(png)
print(out, len(png), "bytes; v range", float(v.min()), float(v.max()), "coverage", float((img > 64).mean()))
