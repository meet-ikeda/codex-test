"""Cut the owner's polygon-brain picture (2026-10-13) out of its own dial and crosshair: src/exobrain/web/brain.png.
Usage: uv run python scripts/cut_brain_picture.py  (edit `src` to the picture's path).
"""
import numpy as np
from PIL import Image
from scipy import ndimage

src = "/private/tmp/claude-501/-Users-meeting-ikeda-Claude/76c5317b-99ab-4b1e-993d-26e2130bcc5e/images/3.webp"
g = np.asarray(Image.open(src).convert("L")).astype(np.float32)
dark = 255 - g
H, W = g.shape
# the crosshair: whole rows / columns that stay dark far outside the brain
colscore = (dark[:150] > 25).sum(0) + (dark[-150:] > 25).sum(0)
rowscore = (dark[:, 380:480] > 25).sum(1) + (dark[:, 1190:1290] > 25).sum(1)
cx = int(colscore.argmax()); cy = int(rowscore.argmax())
print("crosshair at", cx, cy, colscore.max(), rowscore.max())
# the brain: a blob after closing, thin lines removed by opening
m = ndimage.gaussian_filter(dark, 5) > 5.5   # the brain's light fill; thin ring lines blur away
m = ndimage.binary_opening(m, structure=np.ones((9, 9)))
lab, n = ndimage.label(m)
sizes = ndimage.sum(m, lab, range(1, n + 1))
brain = lab == (1 + int(np.argmax(sizes)))
brain = ndimage.binary_fill_holes(brain)
brain = ndimage.binary_dilation(brain, iterations=4)
# paint over the crosshair inside the brain with its neighbours
out = g.copy()
for x in range(cx - 2, cx + 3):
    out[:, x] = np.where(brain[:, x], np.maximum(g[:, cx - 5], g[:, cx + 5]), out[:, x])
for y in range(cy - 2, cy + 3):
    out[y, :] = np.where(brain[y, :], np.maximum(g[cy - 5, :], g[cy + 5, :]), out[y, :])
alpha = ndimage.gaussian_filter(brain.astype(np.float32), 1.5)
ys, xs = np.where(brain)
y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
print("bbox", x0, y0, x1, y1)
rgba = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = np.clip(out[y0:y1, x0:x1], 0, 255)
rgba[..., 3] = np.clip(alpha[y0:y1, x0:x1] * 255, 0, 255)
Image.fromarray(rgba, "RGBA").save("/Users/meeting_ikeda/Claude/exobrain/src/exobrain/web/brain.png", optimize=True)
prev = Image.new("RGB", (x1 - x0, y1 - y0), (235, 120, 120)); prev.paste(Image.fromarray(rgba, "RGBA"), (0, 0), Image.fromarray(rgba, "RGBA"))
prev.save("/private/tmp/claude-501/-Users-meeting-ikeda-Claude/76c5317b-99ab-4b1e-993d-26e2130bcc5e/scratchpad/brain_preview.png")
