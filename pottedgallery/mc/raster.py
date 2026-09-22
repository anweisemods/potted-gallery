"""The software rasteriser, and the camera it draws through.

Orthographic, depth buffered, nearest-neighbour sampled: the same three choices
Minecraft's own inventory rendering makes, which is what lets a render sit next to a
screenshot without looking different.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

from .assets import Assets
from .constants import BLOCK
from .model import Quad, rotation_matrix


class Canvas:
    """Orthographic software rasteriser with a depth buffer and nearest-neighbour sampling.

    Orthographic projection makes affine interpolation of UV and depth exact, so there is
    no perspective correction to get wrong.
    """

    def __init__(self, width: int, height: int):
        self.width, self.height = width, height
        self.color = np.zeros((height, width, 4), dtype=np.float32)
        self.depth = np.full((height, width), np.inf, dtype=np.float32)

    def draw(self, verts: np.ndarray, uv: np.ndarray, tex: np.ndarray,
             shade: float, tint: tuple[float, float, float], blend: bool,
             rect: tuple[int, int, int, int]) -> None:
        for a, b, c in ((0, 1, 2), (0, 2, 3)):
            self._triangle(verts[[a, b, c]], uv[[a, b, c]], tex, shade, tint, blend, rect)

    def _triangle(self, v: np.ndarray, uv: np.ndarray, tex: np.ndarray,
                  shade: float, tint: tuple[float, float, float], blend: bool,
                  rect: tuple[int, int, int, int]) -> None:
        x0 = max(int(math.floor(v[:, 0].min())), 0)
        x1 = min(int(math.ceil(v[:, 0].max())) + 1, self.width)
        y0 = max(int(math.floor(v[:, 1].min())), 0)
        y1 = min(int(math.ceil(v[:, 1].max())) + 1, self.height)
        if x0 >= x1 or y0 >= y1:
            return

        area = ((v[1, 0] - v[0, 0]) * (v[2, 1] - v[0, 1])
                - (v[2, 0] - v[0, 0]) * (v[1, 1] - v[0, 1]))
        if abs(area) < 1e-9:
            return

        ys, xs = np.mgrid[y0:y1, x0:x1]
        px, py = xs + 0.5, ys + 0.5

        def edge(i: int, j: int) -> np.ndarray:
            return ((v[j, 0] - v[i, 0]) * (py - v[i, 1])
                    - (v[j, 1] - v[i, 1]) * (px - v[i, 0])) / area

        w1, w2 = edge(0, 1), edge(1, 2)
        w0 = 1.0 - w1 - w2
        # w2 belongs to vertex 0, w0 to vertex 1, w1 to vertex 2 with this edge ordering.
        bary = np.stack([w2, w0, w1], axis=-1)
        inside = (bary >= -1e-6).all(axis=-1)
        if not inside.any():
            return

        depth = bary @ v[:, 2]
        near = inside & (depth < self.depth[y0:y1, x0:x1])
        if not near.any():
            return

        coords = bary @ uv
        th, tw = tex.shape[0], tex.shape[1]
        tx = np.clip((coords[..., 0] / BLOCK * tw).astype(np.int32), rect[0], rect[2])
        ty = np.clip((coords[..., 1] / BLOCK * th).astype(np.int32), rect[1], rect[3])
        texel = tex[ty, tx]

        rgb = texel[..., :3] * shade * np.asarray(tint, dtype=np.float32)
        alpha = texel[..., 3]

        if blend:
            mask = near & (alpha > 0.004)
            if not mask.any():
                return
            dst = self.color[y0:y1, x0:x1]
            a = alpha[..., None]
            mixed = np.concatenate([rgb * a + dst[..., :3] * (1 - a),
                                    a + dst[..., 3:4] * (1 - a)], axis=-1)
            self.color[y0:y1, x0:x1] = np.where(mask[..., None], mixed, dst)
        else:
            mask = near & (alpha > 0.5)  # cutout: Minecraft's alpha test, not blending
            if not mask.any():
                return
            dst = self.color[y0:y1, x0:x1]
            src = np.concatenate([rgb, np.ones_like(alpha)[..., None]], axis=-1)
            self.color[y0:y1, x0:x1] = np.where(mask[..., None], src, dst)
            zbuf = self.depth[y0:y1, x0:x1]
            self.depth[y0:y1, x0:x1] = np.where(mask, depth, zbuf)

    def to_image(self, downscale: int) -> Image.Image:
        rgba = np.clip(self.color, 0.0, 1.0)
        # Un-premultiplied colour would bleed black into the antialiased edge, so weight the
        # box filter by alpha and divide it back out.
        if downscale > 1:
            s = downscale
            blocks = rgba.reshape(self.height // s, s, self.width // s, s, 4)
            alpha = blocks[..., 3:4]
            weight = alpha.sum(axis=(1, 3))
            rgb = (blocks[..., :3] * alpha).sum(axis=(1, 3)) / np.maximum(weight, 1e-6)
            rgba = np.concatenate([rgb, weight / (s * s)], axis=-1)
        return Image.fromarray((np.clip(rgba, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGBA")


def texel_rect(uv: np.ndarray, tex: np.ndarray) -> tuple[int, int, int, int]:
    """The texels a face's UV rectangle actually covers, as inclusive indices.

    A UV rectangle is half open: `[5, 5, 11, 11]` means texels 5..10, not 5..11. A sample
    landing exactly on the far edge otherwise reads one texel past the rectangle, and on
    `flower_pot.png` -- 206 of whose 256 texels are transparent -- that texel is empty, so
    the alpha test drops the fragment and the backdrop shows through as a hairline seam
    along the edge of every face.
    """
    th, tw = tex.shape[0], tex.shape[1]
    def span(values: np.ndarray, size: int) -> tuple[int, int]:
        lo = int(math.floor(float(values.min()) / BLOCK * size))
        hi = int(math.ceil(float(values.max()) / BLOCK * size)) - 1
        lo = min(max(lo, 0), size - 1)
        return lo, min(max(hi, lo), size - 1)
    x0, x1 = span(uv[:, 0], tw)
    y0, y1 = span(uv[:, 1], th)
    return x0, y0, x1, y1


# --------------------------------------------------------------------------------------
# Camera and framing
# --------------------------------------------------------------------------------------

def camera_matrix(pitch: float, yaw: float) -> np.ndarray:
    """Yaw about Y, then pitch about X. A positive pitch looks down onto the block."""
    return rotation_matrix("x", pitch) @ rotation_matrix("y", yaw)


def project(points: np.ndarray, pitch: float, yaw: float) -> np.ndarray:
    """Block space to camera space, centred on the block.

    The camera looks down -z with +x right and +y up, so a visible face is one whose
    normal has a positive z. Depth is negated along with the image's y axis, which keeps
    the handedness intact -- flipping y alone would silently mirror every render.
    """
    v = points - np.array([BLOCK / 2, BLOCK / 2, BLOCK / 2])
    v = (camera_matrix(pitch, yaw) @ v.T).T
    return np.stack([v[:, 0], -v[:, 1], -v[:, 2]], axis=-1)


Frame = tuple[float, float, float, float]  # x0, y0, x1, y1 in projected units


def measure(quads: list[Quad], pitch: float, yaw: float) -> Frame:
    """Bounding box of the projected silhouette."""
    box = [math.inf, math.inf, -math.inf, -math.inf]
    for quad in quads:
        p = project(quad.pos, pitch, yaw)
        box[0] = min(box[0], float(p[:, 0].min()))
        box[1] = min(box[1], float(p[:, 1].min()))
        box[2] = max(box[2], float(p[:, 0].max()))
        box[3] = max(box[3], float(p[:, 1].max()))
    return tuple(box)


def union(frames: list[Frame], margin: float = 0.04) -> Frame:
    """One frame that holds every model, so all renders share a scale and an origin.

    Framing on the union rather than on a square centred in the block matters: nothing is
    potted below y=0, so a symmetric box would waste the whole lower half of every tile.
    """
    if not frames:
        raise SystemExit("nothing to frame: every model resolved to no geometry")
    x0 = min(f[0] for f in frames)
    y0 = min(f[1] for f in frames)
    x1 = max(f[2] for f in frames)
    y1 = max(f[3] for f in frames)
    pad = max(x1 - x0, y1 - y0) * margin
    return x0 - pad, y0 - pad, x1 + pad, y1 + pad


def frame_size(frame: Frame, longest: int) -> tuple[int, int]:
    width, height = frame[2] - frame[0], frame[3] - frame[1]
    if width >= height:
        return longest, max(1, round(longest * height / width))
    return max(1, round(longest * width / height)), longest


def square_frame(units: float) -> Frame:
    return -units, -units, units, units


# --------------------------------------------------------------------------------------
# Drawing a model
# --------------------------------------------------------------------------------------

def render(assets: Assets, quads: list[Quad], frame: Frame, longest: int, ssaa: int,
           pitch: float, yaw: float) -> Image.Image:
    width, height = frame_size(frame, longest)
    canvas = Canvas(width * ssaa, height * ssaa)
    ppu = (width * ssaa) / (frame[2] - frame[0])  # pixels per projected unit

    camera = camera_matrix(pitch, yaw)
    opaque: list[tuple] = []
    translucent: list[tuple] = []
    for quad in quads:
        tex = assets.texture(quad.texture)
        if tex is None:
            continue

        # Backface culling, against the normal in camera space rather than screen winding.
        # It is what makes a cross model's two coplanar quads resolve: exactly one of
        # them faces the camera.
        if float((camera @ quad.normal)[2]) <= 0.0:
            continue

        p = project(quad.pos, pitch, yaw)
        screen = np.stack([(p[:, 0] - frame[0]) * ppu,
                           (p[:, 1] - frame[1]) * ppu,
                           p[:, 2]], axis=-1)
        tint = (1.0, 1.0, 1.0)
        if quad.tint >= 0:
            tint = ((quad.tint >> 16 & 0xFF) / 255.0,
                    (quad.tint >> 8 & 0xFF) / 255.0,
                    (quad.tint & 0xFF) / 255.0)
        entry = (screen, quad.uv, tex, quad.shade, tint, texel_rect(quad.uv, tex))
        (translucent if assets.translucent(quad.texture) else opaque).append(entry)

    for screen, uv, tex, shade, tint, rect in opaque:
        canvas.draw(screen, uv, tex, shade, tint, False, rect)
    # Translucent quads blend back to front against the depth the opaque pass left behind.
    translucent.sort(key=lambda e: float(e[0][:, 2].max()), reverse=True)
    for screen, uv, tex, shade, tint, rect in translucent:
        canvas.draw(screen, uv, tex, shade, tint, True, rect)

    return canvas.to_image(ssaa)
