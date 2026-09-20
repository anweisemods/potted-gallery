#!/usr/bin/env python3
"""Render a gallery of a flower-pot mod's blocks, plus the item you pot each one with.

Reads the mod's own blockstate/model JSON and pulls the textures it references out of a
vanilla client jar and any dependency jars, then rasterises each model with a small
software renderer that mimics Minecraft's block rendering: the inventory camera angle,
the fixed per-face diffuse shading and the tint layers the client registers at runtime.

Which mod is described by a `gallery.json` in the mod's repository; see the README.

Outputs, into ``build/gallery`` by default:

    blocks/<name>.png   one transparent render per potted variant
    items/<name>.png    the source item's inventory icon
    overview.png        a labelled contact sheet of everything
    index.html          a searchable gallery
    gallery.json        the manifest the page is built from

Needs only Pillow and numpy.
"""

from __future__ import annotations

import argparse
import datetime
import io
import glob
import json
import math
import os
import re
import sys
import zipfile
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

NEAREST = getattr(Image, "Resampling", Image).NEAREST
BOX = getattr(Image, "Resampling", Image).BOX

# --------------------------------------------------------------------------------------
# Minecraft constants
# --------------------------------------------------------------------------------------

# Block space is 0..16 on every axis; the centre of a block is (8, 8, 8).
BLOCK = 16.0

# The angle `minecraft:block/block` uses for its `gui` display transform. Every block you
# have ever seen in an inventory slot or an item frame is drawn from here.
#
# The model file writes the yaw as 225, but that is measured through Minecraft's own GUI
# matrix stack, which mirrors an axis on the way. 135 is the same view in the plain
# right-handed frame this renderer uses -- checked against the furnace, the crafting table
# and the stairs, whose inventory icons all pin down which two side faces you can see.
GUI_PITCH, GUI_YAW = 30.0, 135.0
GUI_SCALE = 0.625

# Item icons keep GUI_YAW, because that is literally what an item frame shows. The potted
# blocks get the mirrored view instead: it puts north and east towards the camera, and
# both vanilla and this mod point a sunflower's face east, so at GUI_YAW the one block
# everybody recognises would be showing its back. Nothing else in the set reads worse.
#
# 240, not the 225 that would be the exact diagonal. A vanilla `cross` plant is two quads
# rotated 45 degrees about Y, so their normals sit on the diagonals at 45 and 315 -- and
# 225 is square-on to the first and *exactly* edge-on to the second. Every cross plant
# (grass, bushes, sugar cane, wildflowers) therefore rendered as a single flat billboard.
# Pitch cannot rescue it: the edge-on quad's normal is horizontal, so its dot product with
# the view direction stays zero however far you tilt. 15 degrees off the diagonal gives the
# second quad 26% of its width back -- enough to read as a plant -- while the pot itself
# still shows two faces at 0.87/0.50 and looks like the same three-quarter view. Past about
# 247.5 the pot starts reading as lopsided, which is the cost of going further.
BLOCK_YAW = 240.0

# Level.getShade(): a constant per face direction, applied instead of real lighting.
SHADE = {"down": 0.5, "up": 1.0, "north": 0.8, "south": 0.8, "west": 0.6, "east": 0.6}

NORMALS = {
    "down": (0.0, -1.0, 0.0),
    "up": (0.0, 1.0, 0.0),
    "north": (0.0, 0.0, -1.0),
    "south": (0.0, 0.0, 1.0),
    "west": (-1.0, 0.0, 0.0),
    "east": (1.0, 0.0, 0.0),
}

# FaceBakery scales a rotated element back out so it still spans its original bounds.
RESCALE = {22.5: 1.0 / math.cos(math.pi / 8.0), 45.0: 1.0 / math.cos(math.pi / 4.0)}

# Biome presets as (temperature, downfall), the two inputs to the grass/foliage colormaps.
BIOMES = {
    "plains": (0.8, 0.4),
    "forest": (0.7, 0.8),
    "jungle": (0.95, 0.9),
    "swamp": (0.8, 0.9),
    "savanna": (2.0, 0.0),
    "default": (0.5, 1.0),
}

def stem_color(age: int) -> int:
    """BlockColors' StemBlock entry, which has no asset-side equivalent to read.

    Exposed because a config can ask for it by name: `{"stem": 7}`.
    """
    return (age * 32) << 16 | (255 - age * 8) << 8 | (age * 4)


# --------------------------------------------------------------------------------------
# Assets
# --------------------------------------------------------------------------------------

class Source:
    """One place to look for assets: a resource directory or a jar."""

    def __init__(self, path: Path):
        self.path = path
        self.zip = None if path.is_dir() else zipfile.ZipFile(path)
        self.entries = frozenset() if self.zip is None else frozenset(self.zip.namelist())

    def read(self, rel: str) -> bytes | None:
        if self.zip is None:
            candidate = self.path / rel
            return candidate.read_bytes() if candidate.is_file() else None
        return self.zip.read(rel) if rel in self.entries else None

    def stems(self, folder: str) -> list[str]:
        """Names of the .json files directly inside `folder`, without the extension."""
        if self.zip is None:
            return [p.stem for p in (self.path / folder).glob("*.json")]
        head = folder + "/"
        return [n[len(head):-len(".json")] for n in self.entries
                if n.startswith(head) and n.endswith(".json") and "/" not in n[len(head):]]

    def __str__(self) -> str:
        return str(self.path)


class Assets:
    """Resolves ``namespace:path`` ids against an ordered list of sources.

    A compat mod reaches across several at once: its own resources, the vanilla client
    jar, the mod it adds support for, and the mod it is an addon of -- PottedDelight's
    models inherit from `notenoughpots:block/template_potted_block` and paint themselves
    with `farmersdelight:` textures.
    """

    def __init__(self, sources: list[Source]):
        self.sources = sources
        self.origin: dict[str, Source] = {}
        self._textures: dict[str, np.ndarray | None] = {}

    @staticmethod
    def split(rid: str, default_ns: str = "minecraft") -> tuple[str, str]:
        ns, _, path = rid.partition(":")
        return (ns, path) if path else (default_ns, ns)

    def read(self, ns: str, kind: str, path: str) -> bytes | None:
        rel = f"assets/{ns}/{kind}/{path}"
        for source in self.sources:
            data = source.read(rel)
            if data is not None:
                self.origin.setdefault(ns, source)
                return data
        return None

    def stems(self, ns: str, kind: str) -> list[str]:
        found: list[str] = []
        for source in self.sources:
            found += source.stems(f"assets/{ns}/{kind}")
        return sorted(dict.fromkeys(found))

    def json(self, ns: str, kind: str, path: str) -> dict | None:
        raw = self.read(ns, kind, path)
        return json.loads(raw) if raw is not None else None

    def texture(self, rid: str) -> np.ndarray | None:
        """A texture as float RGBA in 0..1. Animated textures collapse to their first frame."""
        if rid in self._textures:
            return self._textures[rid]
        ns, path = self.split(rid)
        raw = self.read(ns, "textures", f"{path}.png")
        out = None
        if raw is not None:
            img = Image.open(io.BytesIO(raw)).convert("RGBA")
            meta = self.read(ns, "textures", f"{path}.png.mcmeta")
            if meta is not None and b"animation" in meta and img.height > img.width:
                img = img.crop((0, 0, img.width, img.width))
            out = np.asarray(img, dtype=np.float32) / 255.0
        self._textures[rid] = out
        return out


def colormap_color(assets: Assets, name: str, temperature: float, downfall: float) -> int:
    """GrassColor/FoliageColor: index the 256x256 colormap by temperature and downfall."""
    tex = assets.texture(f"minecraft:colormap/{name}")
    if tex is None:
        return 0xFFFFFF
    t = max(0.0, min(1.0, temperature))
    x = min(int((1.0 - t) * 255.0), tex.shape[1] - 1)
    y = min(int((1.0 - downfall * t) * 255.0), tex.shape[0] - 1)
    r, g, b, _ = tex[y, x]
    return int(r * 255) << 16 | int(g * 255) << 8 | int(b * 255)


# --------------------------------------------------------------------------------------
# Model resolution
# --------------------------------------------------------------------------------------

@dataclass
class Model:
    elements: list[dict] = field(default_factory=list)
    textures: dict[str, str] = field(default_factory=dict)
    display: dict = field(default_factory=dict)
    gui_light: str = "side"
    chain: list[str] = field(default_factory=list)

    def resolve(self, ref: str) -> str | None:
        """Follow ``#name`` texture indirections until a real resource id falls out."""
        for _ in range(8):
            if not ref.startswith("#"):
                return ref or None
            ref = self.textures.get(ref[1:], "")
        return None


def load_model(assets: Assets, rid: str, depth: int = 0) -> Model:
    ns, path = assets.split(rid)
    if depth > 16 or path.startswith("builtin/"):
        return Model(chain=[rid])
    raw = assets.json(ns, "models", f"{path}.json")
    if raw is None:
        return Model(chain=[rid])

    model = load_model(assets, raw["parent"], depth + 1) if "parent" in raw else Model()
    model.chain.insert(0, rid)
    model.textures.update(raw.get("textures", {}))
    if "elements" in raw:  # a child replaces its parent's elements wholesale, never merges
        model.elements = raw["elements"]
    for slot, transform in raw.get("display", {}).items():
        model.display.setdefault(slot, transform)
    if "gui_light" in raw:
        model.gui_light = raw["gui_light"]
    return model


# --------------------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------------------

@dataclass
class Quad:
    pos: np.ndarray       # (4, 3) corners in block space
    uv: np.ndarray        # (4, 2) texture coordinates in 0..16
    normal: np.ndarray    # (3,) outward normal, after the element's own rotation
    texture: str
    shade: float
    tint: int             # -1 for untinted


def default_uv(face: str, f: list[float], t: list[float]) -> list[float]:
    """BlockElement.uvsByFace -- the UV a face gets when the model does not spell one out."""
    return {
        "down": [f[0], BLOCK - t[2], t[0], BLOCK - f[2]],
        "up": [f[0], f[2], t[0], t[2]],
        "north": [BLOCK - t[0], BLOCK - t[1], BLOCK - f[0], BLOCK - f[1]],
        "south": [f[0], BLOCK - t[1], t[0], BLOCK - f[1]],
        "west": [f[2], BLOCK - t[1], t[2], BLOCK - f[1]],
        "east": [BLOCK - t[2], BLOCK - t[1], BLOCK - f[2], BLOCK - f[1]],
    }[face]


def face_corner(face: str, u: float, v: float, f: list[float], t: list[float]) -> tuple:
    """Inverse of default_uv: where on the face does a given UV land."""
    return {
        "down": (u, f[1], BLOCK - v),
        "up": (u, t[1], v),
        "north": (BLOCK - u, BLOCK - v, f[2]),
        "south": (u, BLOCK - v, t[2]),
        "west": (f[0], BLOCK - v, u),
        "east": (t[0], BLOCK - v, BLOCK - u),
    }[face]


def rotation_matrix(axis: str, degrees: float) -> np.ndarray:
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)


def build_quads(model: Model, tints: list) -> list[Quad]:
    quads: list[Quad] = []
    for element in model.elements:
        f, t = [float(x) for x in element["from"]], [float(x) for x in element["to"]]
        rot = element.get("rotation")
        unlit = element.get("light_emission", 0) >= 15
        shaded = element.get("shade", True) and not unlit

        matrix, origin, scale = None, None, None
        if rot:
            matrix = rotation_matrix(rot["axis"], float(rot["angle"]))
            origin = np.array(rot["origin"], dtype=np.float64)
            scale = np.ones(3)
            if rot.get("rescale"):
                factor = RESCALE.get(abs(float(rot["angle"])), 1.0)
                for i, ax in enumerate("xyz"):
                    if ax != rot["axis"]:
                        scale[i] = factor

        for face, spec in element.get("faces", {}).items():
            texture = model.resolve(spec.get("texture", ""))
            if texture is None:
                continue
            # The face always covers the element's own rectangle. A model that states a
            # `uv` is choosing a different slice of the texture to stretch over it, not
            # moving the geometry -- so positions come from the default UV and the stated
            # one only drives sampling.
            du0, dv0, du1, dv1 = default_uv(face, f, t)
            u0, v0, u1, v1 = spec.get("uv") or (du0, dv0, du1, dv1)
            turns = (int(spec.get("rotation", 0)) // 90) % 4

            pos, uv = [], []
            for s, q in ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)):
                pos.append(face_corner(face, du0 + (du1 - du0) * s,
                                       dv0 + (dv1 - dv0) * q, f, t))
                rs, rq = s, q
                for _ in range(turns):  # a face rotation turns the texture, not the quad
                    rs, rq = rq, 1.0 - rs
                uv.append((u0 + (u1 - u0) * rs, v0 + (v1 - v0) * rq))

            points = np.array(pos, dtype=np.float64)
            normal = np.array(NORMALS[face], dtype=np.float64)
            if matrix is not None:
                points = (matrix @ ((points - origin) * scale).T).T + origin
                normal = matrix @ normal

            # Shading follows the rotated normal, the way FaceBakery re-derives a quad's
            # direction after rotating it -- a 45 degree cross plane is lit as north/south.
            axis = max(NORMALS, key=lambda d: float(np.dot(normal, NORMALS[d])))
            tint_index = spec.get("tintindex", -1)
            tint = tints[tint_index] if 0 <= tint_index < len(tints) else -1

            quads.append(Quad(
                pos=points,
                uv=np.array(uv, dtype=np.float64),
                normal=normal,
                texture=texture,
                shade=SHADE[axis] if shaded else 1.0,
                tint=tint,
            ))
    return quads


# --------------------------------------------------------------------------------------
# Rasteriser
# --------------------------------------------------------------------------------------

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


def is_translucent(tex: np.ndarray) -> bool:
    """Since 26.1 the render layer is decided per quad from the texture's own transparency."""
    alpha = tex[..., 3]
    partial = np.count_nonzero((alpha > 0.03) & (alpha < 0.97))
    return partial > 0.01 * alpha.size


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
        (translucent if is_translucent(tex) else opaque).append(entry)

    for screen, uv, tex, shade, tint, rect in opaque:
        canvas.draw(screen, uv, tex, shade, tint, False, rect)
    # Translucent quads blend back to front against the depth the opaque pass left behind.
    translucent.sort(key=lambda e: float(e[0][:, 2].max()), reverse=True)
    for screen, uv, tex, shade, tint, rect in translucent:
        canvas.draw(screen, uv, tex, shade, tint, True, rect)

    return canvas.to_image(ssaa)


# --------------------------------------------------------------------------------------
# The mod's catalogue
# --------------------------------------------------------------------------------------

ENUM_ENTRY = re.compile(r"^\s+(POTTED_[A-Z0-9_]+)\(Blocks\.([A-Z0-9_]+)\s*[,)]")

# The enum is already grouped by the Minecraft version each plant arrived in, with a bare
# `// 1.20+` line above each group. Trailing comments on an entry are notes, not markers.
SINCE_MARKER = re.compile(r"^\s*//\s*([0-9][0-9.]*)\+\s*$")


@dataclass
class Variant:
    name: str            # potted_acacia_leaves
    source: str          # acacia_leaves
    item: str            # the item you right-click the pot with
    model: str           # notenoughpots:block/potted_acacia_leaves
    label: str           # Acacia Leaves
    since: str           # earliest Minecraft version that has the plant


def read_properties(path: Path) -> dict[str, str]:
    props = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()
    return props


def round_corners(icon: Image.Image, ratio: float = 0.2237) -> Image.Image:
    """Round a square icon the way a platform would.

    0.2237 is the corner radius Apple's icon grid uses, as a fraction of the side, and
    it is what every "squircle" approximation is aiming at -- close enough that a
    rounded rectangle reads correctly at favicon sizes. The mask is drawn at 4x and
    downsampled so the curve is antialiased rather than stepped.
    """
    size = icon.size[0]
    scale = 4
    mask = Image.new("L", (size * scale, size * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size * scale - 1, size * scale - 1),
        radius=round(size * scale * ratio), fill=255)
    mask = mask.resize((size, size), BOX)
    out = icon.copy()
    alpha = out.getchannel("A")
    out.putalpha(Image.composite(alpha, Image.new("L", out.size, 0), mask))
    return out


def crop_to_ink(art: Image.Image, threshold: int = 32) -> Image.Image:
    """Trim a wordmark to the part of it you can actually see.

    `getbbox()` stops at alpha 0, which leaves whatever invisible halo the artwork was
    exported with -- NotEnoughPots' banner is 286px tall and only 250px of that is above
    12.5% alpha. That margin is why the header could not size two mods' wordmarks the
    same way: it scales the art but not the ink inside it.
    """
    alpha = np.asarray(art)[..., 3]
    rows, columns = np.nonzero(alpha >= threshold)
    if not rows.size:
        return art
    return art.crop((int(columns.min()), int(rows.min()),
                     int(columns.max()) + 1, int(rows.max()) + 1))


def find_banner(config: Config) -> Path | None:
    """The mod's wordmark, if it has one.

    `.github/assets/banner.png`, or whatever `banner` names. A wordmark already
    spells the mod out, so when one exists the header shows it instead of the icon and
    the name, and the heading survives only for screen readers and the tab title.
    """
    if config.banner is not None:
        if not config.banner.exists():
            raise SystemExit(f"banner not found: {config.banner}")
        return config.banner
    # `.github/assets/` first: the banner is presentation art for the repository, not
    # something the mod jar ships, and that is where GitHub-facing images belong. The
    # repository root is accepted too, but it is not where this should live.
    for relative in (".github/assets/banner.png", "banner.png"):
        candidate = config.repo / relative
        if candidate.exists():
            return candidate
    return None


def find_logo(config: Config) -> Path | None:
    """The mod's own icon, for the gallery header.

    `gallery.json` can name one with `logo`. Otherwise this looks for `<mod_id>.png` at
    each resource root, which is where both mods keep theirs -- it is the file
    `fabric.mod.json` points its `"icon": "${mod_id}.png"` at, so it travels with the
    repository and needs no CDN.
    """
    if config.logo is not None:
        if not config.logo.exists():
            raise SystemExit(f"logo not found: {config.logo}")
        return config.logo
    for root in config.resources:
        candidate = config.repo / root / f"{config.mod_id}.png"
        if candidate.exists():
            return candidate
    return None


def version_key(version: str) -> tuple:
    """Sort key putting the newest Minecraft version first.

    Versions are dotted numbers, so a tuple of ints orders them correctly across the
    1.x era and the 26.x renumbering alike: (1, 21, 5) < (26, 3). Negating each part
    sorts descending while leaving the rest of the sort key ascending. A missing or
    unparsable version sorts last rather than raising -- PottedDelight has no enum to
    read markers from and every variant's `since` is "".

    The padding is what makes 1.20.3 sort ahead of 1.20: an unpadded (-1, -20, -3) is
    *greater* than (-1, -20), because a shorter tuple compares as smaller, which would
    put the older version first.
    """
    if not version:
        return (1, ())
    try:
        parts = [int(part) for part in version.split(".")]
    except ValueError:
        return (1, ())
    parts = (parts + [0, 0, 0, 0])[:4]
    return (0, tuple(-part for part in parts))


def read_enum(path: Path, base: str) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Source block, "since" version and declaration order, from a Java enum.

    Optional. A mod whose enum wraps something other than `Blocks.X` -- PottedDelight's
    wraps `Supplier<Block>` from Farmer's Delight, and lives in the loader modules rather
    than in common -- simply leaves it out and gets everything from the blockstates.
    """
    blocks: dict[str, str] = {}
    since: dict[str, str] = {}
    order: list[str] = []
    current = base
    for line in path.read_text(encoding="utf-8").splitlines():
        marker = SINCE_MARKER.match(line)
        if marker:
            current = marker.group(1)
            continue
        entry = ENUM_ENTRY.match(line)
        if entry is None:
            continue
        name = entry.group(1).lower()
        blocks[name] = entry.group(2).lower()
        since[name] = current
        order.append(name)
    return blocks, since, order


def read_catalogue(assets: Assets, config: Config) -> list[Variant]:
    blocks, since, order = {}, {}, []
    if config.enum_java is not None:
        blocks, since, order = read_enum(config.enum_java, config.since_base)

    names = assets.stems(config.mod_id, "blockstates")
    # Newest Minecraft version first, and inside a version the enum's own declaration
    # order -- which groups related blocks (all the corals, all the leaves) far better
    # than alphabetical does. A mod with no enum has no version data at all, so `ranked`
    # and `since` are both empty and this degrades to plain alphabetical.
    ranked = {name: i for i, name in enumerate(order)}
    names.sort(key=lambda n: (version_key(since.get(n, "")), ranked.get(n, len(ranked)), n))

    variants = []
    for name in names:
        blockstate = assets.json(config.mod_id, "blockstates", f"{name}.json")
        state = (blockstate or {}).get("variants", {}).get("")
        if isinstance(state, list):
            state = state[0]
        if not state or "model" not in state:
            print(f"  ! {name} has no plain variant, skipping", file=sys.stderr)
            continue
        plain = name[len(config.prefix):] if name.startswith(config.prefix) else name
        block = blocks.get(name, plain)
        variants.append(Variant(
            name=name,
            source=block,
            item=config.item_aliases.get(block, block),
            model=state["model"],
            label=plain.replace("_", " ").title(),
            since=since.get(name, config.since_base),
        ))
    return variants


# --------------------------------------------------------------------------------------
# Tints
# --------------------------------------------------------------------------------------

def find_model_node(node) -> dict | None:
    """Walk an ``assets/<ns>/items/*.json`` definition down to a concrete model node.

    Conditional and range-dispatched items resolve to their fallback, which is the
    appearance you get holding a fresh one.
    """
    if not isinstance(node, dict):
        return None
    if node.get("type", "").endswith(":model") and "model" in node:
        return node
    for key in ("fallback", "on_false", "on_true", "model", "models", "cases", "entries"):
        value = node.get(key)
        if value is None:
            continue
        for candidate in (value if isinstance(value, list) else [value]):
            if isinstance(candidate, dict) and "type" not in candidate and "model" in candidate:
                candidate = candidate["model"]
            found = find_model_node(candidate)
            if found is not None:
                return found
    return None


def item_definition(assets: Assets, namespace: str, item: str) -> dict | None:
    """An item's appearance, from either layout Minecraft has used.

    1.21.4 introduced `assets/<ns>/items/<id>.json`, which also carries the item's tints.
    Before that the item model lived at `assets/<ns>/models/item/<id>.json` and tinting
    was code only, so an older pack gets no tints from here and relies on the config.
    """
    modern = assets.json(namespace, "items", f"{item}.json")
    if modern is not None:
        return modern
    if assets.read(namespace, "models", f"item/{item}.json") is not None:
        return {"model": {"type": "minecraft:model", "model": f"{namespace}:item/{item}"}}
    return None


def decode_tints(specs: list, grass: int, foliage: int) -> list[int]:
    out = []
    for spec in specs:
        kind = spec.get("type", "") if isinstance(spec, dict) else ""
        if kind.endswith(":constant"):
            out.append(int(spec.get("value", -1)) & 0xFFFFFF)
        elif kind.endswith(":grass"):
            out.append(grass)
        elif kind.endswith(":foliage"):
            out.append(foliage)
        else:
            out.append(-1)
    return out


def config_tint(spec, grass: int, foliage: int) -> int:
    """One entry of the config's `tints` table."""
    if spec == "grass":
        return grass
    if spec == "foliage":
        return foliage
    if isinstance(spec, dict) and "stem" in spec:
        return stem_color(int(spec["stem"]))
    if isinstance(spec, str):
        return int(spec.lstrip("#"), 16)
    return int(spec)


def tint_layers(assets: Assets, variant: Variant, item_def: dict | None,
                grass: int, foliage: int, coded: dict) -> list[int]:
    """The colour of each ``tintindex`` layer, mirroring what the client registers.

    On 1.21.4+ most of it is already stated in the vanilla item definition (leaves carry
    a constant, the grasses carry ``minecraft:grass``). The rest lives only in the mod's
    client class, so it has to be restated in the config.
    """
    layers = coded.get(variant.source)
    if layers is not None:
        return [config_tint(layer, grass, foliage) for layer in layers]
    node = find_model_node(item_def) if item_def else None
    return decode_tints(node.get("tints", []), grass, foliage) if node else []


# --------------------------------------------------------------------------------------
# Item icons
# --------------------------------------------------------------------------------------

def render_item(assets: Assets, item_def: dict | None, size: int, ssaa: int,
                grass: int, foliage: int) -> Image.Image | None:
    """Draw an item the way an inventory slot or an item frame would."""
    node = find_model_node(item_def) if item_def else None
    if node is None:
        return None
    model = load_model(assets, node["model"])
    tints = decode_tints(node.get("tints", []), grass, foliage)

    if not model.elements:
        # `item/generated`: a stack of flat sprites, drawn without any diffuse shading.
        layers = []
        for index in range(8):
            ref = model.resolve(f"#layer{index}")
            tex = assets.texture(ref) if ref else None
            if tex is None:
                break
            rgba = tex.copy()
            if index < len(tints) and tints[index] >= 0:
                rgba[..., :3] *= np.array([(tints[index] >> 16 & 0xFF) / 255.0,
                                           (tints[index] >> 8 & 0xFF) / 255.0,
                                           (tints[index] & 0xFF) / 255.0], dtype=np.float32)
            layers.append(Image.fromarray((rgba * 255.0 + 0.5).astype(np.uint8), "RGBA"))
        if not layers:
            return None
        flat = layers[0]
        for layer in layers[1:]:
            flat = Image.alpha_composite(flat, layer)
        return flat.resize((size, size), NEAREST)

    gui = model.display.get("gui", {})
    pitch, mc_yaw = (gui.get("rotation") or [GUI_PITCH, -GUI_YAW, 0.0])[:2]
    # Same mirror as GUI_YAW: a yaw written for Minecraft's GUI stack is negated here.
    pitch, yaw = float(pitch), -float(mc_yaw)
    scale = (gui.get("scale") or [GUI_SCALE])[0] or GUI_SCALE
    quads = build_quads(model, tints)
    if not quads:
        return None
    if model.gui_light == "front":
        for quad in quads:
            quad.shade = 1.0
    # A block item fills the slot exactly when the view spans 16 units divided by the
    # transform's scale, which is how `minecraft:block/block` lands on 0.625.
    return render(assets, quads, square_frame(BLOCK / 2.0 / scale), size, ssaa, pitch, yaw)


# --------------------------------------------------------------------------------------
# The Minecraft font, read out of the client jar
# --------------------------------------------------------------------------------------

class McFont:
    """Renders text with ``textures/font/ascii.png``, the 16x16 grid of 8x8 glyphs."""

    def __init__(self, assets: Assets):
        tex = assets.texture("minecraft:font/ascii")
        self.sheet = None
        if tex is None or tex.shape[0] != tex.shape[1] or tex.shape[0] % 16:
            return
        self.cell = tex.shape[0] // 16
        self.sheet = (tex * 255.0 + 0.5).astype(np.uint8)
        # Glyphs are variable width: trim to the rightmost lit column, then one pixel gap.
        self.widths = {0x20: 4}
        for code in range(0x21, 0x7F):
            columns = np.nonzero(self._glyph(code)[..., 3].any(axis=0))[0]
            self.widths[code] = int(columns[-1]) + 2 if columns.size else 4

    def _glyph(self, code: int) -> np.ndarray:
        row, col = divmod(code, 16)
        c = self.cell
        return self.sheet[row * c:(row + 1) * c, col * c:(col + 1) * c]

    def height(self, scale: int) -> int:
        return (self.cell if self.sheet is not None else 8) * scale

    def width(self, text: str, scale: int) -> int:
        if self.sheet is None:
            return len(text) * 6 * scale
        return sum(self.widths.get(ord(ch), 4) for ch in text) * scale

    def draw(self, target: Image.Image, xy: tuple[int, int], text: str, scale: int,
             colour: tuple[int, int, int], shadow: bool = True) -> None:
        if self.sheet is None:
            from PIL import ImageDraw
            ImageDraw.Draw(target).text(xy, text, fill=colour)
            return
        if shadow:
            dim = tuple(int(channel * 0.25) for channel in colour)
            self.draw(target, (xy[0] + scale, xy[1] + scale), text, scale, dim, shadow=False)
        x, y = xy
        for ch in text:
            code = ord(ch)
            if 0x20 < code < 0x7F:
                glyph = self._glyph(code).copy()
                glyph[..., 0], glyph[..., 1], glyph[..., 2] = colour
                sprite = Image.fromarray(glyph, "RGBA")
                if scale != 1:
                    sprite = sprite.resize((self.cell * scale,) * 2, NEAREST)
                target.alpha_composite(sprite, (x, y))
            x += self.widths.get(code, 4) * scale


# --------------------------------------------------------------------------------------
# Geometry export, for the turntable in the web gallery
# --------------------------------------------------------------------------------------

def texture_filename(rid: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", rid.lower()).strip("_") + ".png"


def export_geometry(assets: Assets, quads: dict[str, list[Quad]], out: Path) -> int:
    """Write every model as quads plus the textures they use, for the WebGL viewer.

    Textures go out as individual files rather than one atlas: a model needs about six of
    them, they are 16x16, and keeping `uv / 16` exact costs nothing and cannot bleed.
    """
    (out / "textures").mkdir(parents=True, exist_ok=True)
    written: set[str] = set()
    models = {}

    for name, quad_list in quads.items():
        textures: list[str] = []
        exported = []
        for quad in quad_list:
            tex = assets.texture(quad.texture)
            if tex is None:
                continue
            file = texture_filename(quad.texture)
            if file not in written:
                image = Image.fromarray((np.clip(tex, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8),
                                        "RGBA")
                image.save(out / "textures" / file)
                written.add(file)
            if file not in textures:
                textures.append(file)

            # WebGL culls by winding, so make every quad wind counter-clockwise as seen
            # from its own front. Cross models depend on it: two coplanar quads, opposite
            # normals, and only the one facing you should survive.
            pos, uv = quad.pos, quad.uv
            facing = np.cross(pos[1] - pos[0], pos[2] - pos[0])
            if float(np.dot(facing, quad.normal)) < 0.0:
                pos, uv = pos[::-1], uv[::-1]

            x0, y0, x1, y1 = texel_rect(uv, tex)
            th, tw = tex.shape[0], tex.shape[1]
            entry = {
                "i": textures.index(file),
                "p": [round(float(c), 3) for c in pos.reshape(-1)],
                "u": [round(float(c), 3) for c in uv.reshape(-1)],
                # Centres of the first and last texel the face covers; the shader clamps
                # into this so a sample on the far edge cannot read past the rectangle.
                "r": [round((x0 + 0.5) / tw, 6), round((y0 + 0.5) / th, 6),
                      round((x1 + 0.5) / tw, 6), round((y1 + 0.5) / th, 6)],
            }
            if quad.shade != 1.0:
                entry["s"] = round(quad.shade, 3)
            if quad.tint >= 0:
                entry["c"] = [round((quad.tint >> shift & 0xFF) / 255.0, 4)
                              for shift in (16, 8, 0)]
            if is_translucent(tex):
                entry["b"] = 1
            exported.append(entry)
        models[name] = {"t": textures, "q": exported}

    payload = json.dumps(models, separators=(",", ":"))
    (out / "models.js").write_text(f"window.NEP_MODELS = {payload};\n", encoding="utf-8")
    return len(written)


# --------------------------------------------------------------------------------------
# The contact sheet
# --------------------------------------------------------------------------------------

# The sheet is drawn the way Minecraft draws a GUI: square corners, hard one-pixel
# borders, bevelled inventory slots, and the game's own font. Every measurement below is
# a multiple of `u`, one Minecraft pixel at the tile size the sheet composites at, so the
# layout sits on the same grid as the art rather than beside it.

# Six across is a sheet you can read a label on. A mod overrides it with "columns" in its
# gallery.json, which is what the workflow passes along, and --columns beats both.
OVERVIEW_COLUMNS = 8

THEMES = {
    "dark": {
        "bg": (17, 19, 24), "tile": (27, 30, 37), "edge": (44, 49, 60),
        "slot": (19, 21, 26), "slot_hi": (52, 58, 71), "slot_lo": (8, 9, 12),
        "text": (228, 233, 241), "muted": (135, 145, 163), "accent": (124, 207, 106),
        "shadow": True,
    },
    "light": {
        "bg": (245, 246, 249), "tile": (255, 255, 255), "edge": (223, 227, 234),
        "slot": (226, 230, 237), "slot_hi": (255, 255, 255), "slot_lo": (196, 202, 213),
        "text": (28, 32, 40), "muted": (108, 118, 134), "accent": (47, 125, 51),
        "shadow": False,
    },
}


def wrap(font: McFont, text: str, scale: int, limit: int, lines: int = 2) -> list[str]:
    out, current = [], ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if current and len(out) < lines - 1 and font.width(candidate, scale) > limit:
            out.append(current)
            current = word
        else:
            current = candidate
    out.append(current)
    return out


# --------------------------------------------------------------------------------------
# Drawing primitives. Every one of them takes a half-open box, so a box's width is
# `x1 - x0` and two boxes sharing an edge never overlap on it.
# --------------------------------------------------------------------------------------

Box = tuple[int, int, int, int]
Colour = tuple[int, int, int]


def fill(draw, box: Box, colour: Colour) -> None:
    x0, y0, x1, y1 = box
    if x1 > x0 and y1 > y0:
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=colour + (255,))


def panel(draw, box: Box, face: Colour, border: Colour, weight: int) -> None:
    """A flat panel: one hard border, square corners, no radius anywhere."""
    x0, y0, x1, y1 = box
    fill(draw, box, face)
    fill(draw, (x0, y0, x1, y0 + weight), border)
    fill(draw, (x0, y1 - weight, x1, y1), border)
    fill(draw, (x0, y0 + weight, x0 + weight, y1 - weight), border)
    fill(draw, (x1 - weight, y0 + weight, x1, y1 - weight), border)


def inventory_slot(draw, box: Box, theme: dict, weight: int) -> None:
    """An inventory slot, sunk into the card: dark along the top and left, lit along the
    bottom and right. The corners are mitred the way the game's widget texture is."""
    x0, y0, x1, y1 = box
    fill(draw, box, theme["slot"])
    fill(draw, (x0, y0, x1 - weight, y0 + weight), theme["slot_lo"])
    fill(draw, (x0, y0, x0 + weight, y1 - weight), theme["slot_lo"])
    fill(draw, (x1 - weight, y0, x1, y1), theme["slot_hi"])
    fill(draw, (x0 + weight, y1 - weight, x1, y1), theme["slot_hi"])


def marker(draw, x: int, y: int, size: int, colour: Colour, shadow: bool) -> None:
    """The separator between two facts: a square pip, because `ascii.png` has no middle
    dot -- McFont only maps 0x21..0x7F, so the glyph at that code point is unrelated."""
    if shadow:
        dim = tuple(int(channel * 0.25) for channel in colour)
        fill(draw, (x + size // 2, y + size // 2, x + size + size // 2, y + size + size // 2),
             dim)
    fill(draw, (x, y, x + size, y + size), colour)


# --------------------------------------------------------------------------------------
# The grid
# --------------------------------------------------------------------------------------

def build_overview(variants: list[Variant], blocks: dict[str, Image.Image],
                   items: dict[str, Image.Image], font: McFont, theme: dict,
                   columns: int, art: tuple[int, int], title: str, version: str,
                   minecraft: str, footer: str, newest: str = "") -> Image.Image:
    from PIL import ImageDraw

    art_w, art_h = art
    # One Minecraft pixel. Every length below is a whole number of these, so nothing can
    # land on a half pixel and the sheet scales as a single piece of pixel art.
    u = max(1, max(art_w, art_h) // 64)
    # McFont only scales by whole numbers, so the tag reads at the body size or at half
    # it, with nothing in between. Half was too quiet for the one fact a card carries
    # besides its name.
    body, tag_size, title_size = u, u, u * 2
    line_h = font.height(body) + u * 2          # leading, so wrapped labels are not set solid
    rule = u                                    # one weight of stroke on the whole sheet
    pad = u * 8                                 # a card's inner padding, all four sides
    gutter = u * 4                              # between two cards
    margin = u * 24                             # around the whole sheet
    step = u * 6                                # between a card's three bands
    split = u * 20                              # between the masthead, the grid and the footer

    shadow = theme["shadow"]
    has_slot = bool(items)
    slot_w = u * 18                             # the game's own slot: 16 for the item, 1 each side
    icon_w = u * 16

    # Wrap first, then size the card to what the text actually needs, so no label can
    # overflow into its neighbour however long a block's name turns out to be.
    labels = {v.name: wrap(font, v.label, body, art_w, lines=3) for v in variants}
    rows_of_text = max(len(lines) for lines in labels.values())
    widest_label = max(font.width(line, body) for lines in labels.values() for line in lines)
    widest_tag = max((font.width(v.since, tag_size) for v in variants if v.since), default=0)

    meta_h = max(font.height(tag_size) if widest_tag else 0, slot_w if has_slot else 0)
    # The tag and the slot sit at opposite ends of the meta band and must never meet.
    meta_w = widest_tag + (gutter + slot_w if has_slot else 0)
    card_w = max(art_w, widest_label, meta_w) + pad * 2
    # The last line carries no trailing leading, so the padding under the label measures
    # the same as the padding over the meta band.
    label_h = line_h * rows_of_text - (line_h - font.height(body))
    card_h = pad + (meta_h + step if meta_h else 0) + art_h + step + label_h + pad

    rows = math.ceil(len(variants) / columns)
    grid_w = columns * card_w + (columns - 1) * gutter

    # The masthead is two columns of the same shape: a value at the heading size over the
    # thing that qualifies it at the caption size. Left, the mod's name over its versions;
    # right, how many blocks are on the sheet over the word for them. Two type sizes and
    # two colours for the whole band, which is what holds the three facts together --
    # anything set in `text` is a value, anything in `muted` says what that value is.
    #
    # The versions share the one line because the pair is a single fact: this build of the
    # mod, for that build of the game. One pip of space between them, and the mod's own
    # version leads in `text` because it qualifies the name directly.
    total, unit = str(len(variants)), "variants"
    gap = u * 3
    versions_w = font.width(version, body) + font.width(minecraft, body) \
        + (gap if version and minecraft else 0)

    # The two columns must not meet, however narrow the grid is. A mod asking for three
    # columns has a grid narrower than its own name set at the heading size.
    apart = u * 16
    content_w = max(grid_w,
                    font.width(title, title_size) + apart + font.width(total, title_size),
                    versions_w + apart + font.width(unit, body))
    width = content_w + margin * 2
    grid_x = (width - grid_w) // 2              # centred, so a widened sheet stays balanced
    right = width - margin

    head_y = margin
    cap_y = head_y + font.height(title_size) + u * 4
    # No rule above the grid or below it: the cards already draw a hard top and bottom
    # edge across the full span, so a second line beside them was drawing the same border
    # twice. White space separates the three bands instead.
    grid_y = cap_y + font.height(body) + split

    grid_h = rows * card_h + (rows - 1) * gutter
    foot_y = grid_y + grid_h + split
    height = foot_y + font.height(body) + margin

    sheet = Image.new("RGBA", (width, height), theme["bg"] + (255,))
    draw = ImageDraw.Draw(sheet)

    font.draw(sheet, (margin, head_y), title, title_size, theme["text"], shadow)
    font.draw(sheet, (right - font.width(total, title_size), head_y), total, title_size,
              theme["text"], shadow)

    x = margin
    if version:
        font.draw(sheet, (x, cap_y), version, body, theme["text"], shadow)
        x += font.width(version, body)
        if minecraft:
            x += gap
    if minecraft:
        font.draw(sheet, (x, cap_y), minecraft, body, theme["muted"], shadow)
    font.draw(sheet, (right - font.width(unit, body), cap_y), unit, body,
              theme["muted"], shadow)

    for index, variant in enumerate(variants):
        row, col = divmod(index, columns)
        x = grid_x + col * (card_w + gutter)
        y = grid_y + row * (card_h + gutter)
        panel(draw, (x, y, x + card_w, y + card_h), theme["tile"], theme["edge"], rule)

        # The meta band is the card's header: which version the pot arrived in on the
        # left, what you pot it with on the right. Keeping the slot up here is what stops
        # it landing on top of a tall plant, which is where it used to sit.
        left, right = x + pad, x + card_w - pad
        if variant.since:
            tag_y = y + pad + (meta_h - font.height(tag_size)) // 2
            font.draw(sheet, (left, tag_y), variant.since, tag_size,
                      theme["accent"] if variant.since == newest else theme["muted"], shadow)

        icon = items.get(variant.name)
        if has_slot and icon is not None:
            slot_y = y + pad + (meta_h - slot_w) // 2
            inventory_slot(draw, (right - slot_w, slot_y, right, slot_y + slot_w),
                           theme, rule)
            sheet.alpha_composite(icon.resize((icon_w, icon_w), BOX),
                                  (right - slot_w + u, slot_y + u))

        art_y = y + pad + (meta_h + step if meta_h else 0)
        block = blocks.get(variant.name)
        if block is not None:
            sheet.alpha_composite(block, (x + (card_w - art_w) // 2, art_y))

        # Every card reserves room for the longest name on the sheet, and the label packs
        # to the bottom of it. So a one-line name sits on the same baseline as the last
        # line of a three-line one, and the slack it does not use lands above the label,
        # beside the artwork, where it reads as breathing room instead of a ragged edge.
        lines = labels[variant.name]
        text_y = art_y + art_h + step + line_h * (rows_of_text - len(lines))
        for line in lines:
            font.draw(sheet, (x + (card_w - font.width(line, body)) // 2, text_y),
                      line, body, theme["text"], shadow)
            text_y += line_h

    font.draw(sheet, (margin, foot_y), footer, body, theme["muted"], shadow)
    return sheet


# --------------------------------------------------------------------------------------
# Web gallery
# --------------------------------------------------------------------------------------

# The page is authored as three files -- markup, stylesheet, script -- because a single
# 1200-line HTML file was getting hard to read. They are inlined at build time, so the
# gallery still ships one self-contained index.html beside its images.
PAGE_TEMPLATE = Path(__file__).with_name("page.html")
PAGE_STYLE = Path(__file__).with_name("page.css")
PAGE_SCRIPT = Path(__file__).with_name("page.js")

STYLE_TAG = '<link rel="stylesheet" href="page.css">'
ICON_LINKS = ('<link rel="icon" type="image/png" href="favicon.png">\n'
              '<link rel="apple-touch-icon" href="apple-touch-icon.png">')


def footer_markup(author: str, footer: str) -> str:
    """The page footer: who made it, and where the source lives.

    `footer` is the same repository the overview sheet prints along its bottom edge,
    so the two cannot name different places. A repository link beats a profile link
    here because the page is about one mod, and that is the mod's repository.
    """
    if not author and not footer:
        return ""
    url = footer if footer.startswith(("http://", "https://")) else f"https://{footer}"
    year = datetime.date.today().year
    parts = ['<footer class="footer">', '  <div class="wrap">']
    if author:
        parts.append(f"    <p>© {year} {escape(author)}</p>")
    if footer:
        parts.append(f'    <a href="{escape(url, True)}">{escape(footer)}</a>')
    parts += ["  </div>", "</footer>"]
    return "\n".join(parts)


# Stores the page knows how to label and badge. Anything else in `downloads` still
# renders -- title-cased, with the generic external-link mark -- so a mod can list a
# fourth place without this file having to learn about it first.
STORES = {
    "modrinth": ("Modrinth", "i-modrinth"),
    "curseforge": ("CurseForge", "i-curseforge"),
    "github": ("GitHub", "i-external"),
}


def downloads_markup(downloads: dict[str, str]) -> str:
    """The masthead's download button and the menu it opens.

    Returns "" when the mod declares no stores, and then page.js leaves the whole
    control alone -- there is no empty button and no dead menu.

    It borrows .picker/.picker-menu rather than introducing a second popup: the menu
    surface, the open animation and the clamp that keeps it on screen are all defined
    once, for the version picker, and a download list is the same object with rows
    that link out instead of rows that select.
    """
    if not downloads:
        return ""
    rows = []
    for key, url in downloads.items():
        label, icon = STORES.get(key, (key.replace("-", " ").title(), "i-external"))
        rows.append(
            f'        <a class="menu-link" role="menuitem" href="{escape(url, True)}"\n'
            f'           target="_blank" rel="noopener noreferrer">\n'
            f'          <svg class="mark" width="20" height="20" aria-hidden="true">'
            f'<use href="#{icon}"/></svg>\n'
            f"          {escape(label)}\n"
            f'          <svg class="go" width="20" height="20" aria-hidden="true">'
            f'<use href="#i-external"/></svg>\n'
            f"        </a>"
        )
    return (
        '<div class="picker" id="downloads">\n'
        '        <button class="btn-text" id="downloads-button" type="button"\n'
        '                aria-haspopup="menu" aria-expanded="false">\n'
        '          <svg class="lead" width="20" height="20" aria-hidden="true">'
        '<use href="#i-download"/></svg>\n'
        '          <span class="label">Download</span>\n'
        '          <svg class="chevron" width="20" height="20" aria-hidden="true">'
        '<use href="#i-chevron-down"/></svg>\n'
        "        </button>\n"
        '        <div class="picker-menu" id="downloads-menu" role="menu"\n'
        '             aria-label="Download" hidden>\n'
        + "\n".join(rows)
        + "\n        </div>\n      </div>"
    )


def mark_markup(heading: str, logo: str | None, banner: str | None) -> str:
    """The header's brand block.

    With a wordmark the name is already drawn, so the <h1> goes visually hidden rather
    than being repeated in type beside it -- it still carries the document outline and
    is what a screen reader announces. Without one, the icon and a real heading.
    """
    if banner:
        return (f'<img class="wordmark" src="{banner}" alt="">\n'
                f'    <h1 class="visually-hidden">{heading}</h1>')
    mark = f'<img class="logo" src="{logo}" alt="">\n    ' if logo else ""
    return f'{mark}<h1>{heading}</h1>'
SCRIPT_TAG = '<script src="page.js"></script>'


def inline_assets(page: str) -> str:
    """Fold page.css and page.js into the markup.

    The tags they replace are real ones, so page.html stays a working document you can
    open straight from the source tree while editing.
    """
    style = PAGE_STYLE.read_text(encoding="utf-8")
    script = PAGE_SCRIPT.read_text(encoding="utf-8")
    if "</script" in script:
        raise SystemExit("page.js contains '</script', which would close the tag it is "
                         "inlined into")
    for tag in (STYLE_TAG, SCRIPT_TAG):
        if tag not in page:
            raise SystemExit(f"page.html no longer contains {tag!r}")
    newline = "\n"
    return (page.replace(STYLE_TAG, f"<style>{newline}{style}</style>")
                .replace(SCRIPT_TAG, f"<script>{newline}{script}</script>"))


def write_html(path: Path, title: str, heading: str, subheading: str,
               manifest: dict, aspect: float, logo: str | None = None,
               banner: str | None = None, icon: bool = False,
               footer: str = "", downloads: str = "") -> None:
    page = (inline_assets(PAGE_TEMPLATE.read_text(encoding="utf-8"))
            .replace("__ASPECT__", f"{aspect:.4f}")
            .replace("__TITLE__", title)
            .replace("__MARK__", mark_markup(heading, logo, banner))
            .replace("__ICONS__", ICON_LINKS if icon else "")
            .replace("__DOWNLOADS__", downloads)
            .replace("__FOOTER__", footer)
            .replace("__HEADING__", heading)
            .replace("__SUBHEADING__", subheading)
            .replace("__DATA__", json.dumps(manifest).replace("</", "<\\/")))
    path.write_text(page, encoding="utf-8")


# --------------------------------------------------------------------------------------
# Project configuration
# --------------------------------------------------------------------------------------

@dataclass
class Config:
    """A `gallery.json` sitting in the mod's repository. Every field has a default."""

    repo: Path
    mod_id: str
    name: str
    version: str = ""
    minecraft: str = ""
    footer: str = ""
    author: str = ""
    prefix: str = "potted_"
    resources: list[Path] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    item_namespace: str = "minecraft"
    item_aliases: dict[str, str] = field(default_factory=dict)
    columns: int = 0
    downloads: dict[str, str] = field(default_factory=dict)
    tints: dict[str, list] = field(default_factory=dict)
    enum_java: Path | None = None
    logo: Path | None = None
    banner: Path | None = None
    since_base: str = ""
    properties: dict[str, str] = field(default_factory=dict)


def load_config(path: Path) -> Config:
    raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    repo = path.parent.resolve()

    # gradle.properties is the mod's own source of truth; the config only overrides it.
    props_file = repo / "gradle.properties"
    props = read_properties(props_file) if props_file.is_file() else {}

    mod_id = raw.get("mod_id") or props.get("mod_id")
    if not mod_id:
        raise SystemExit(f"{path}: no mod_id, and none in gradle.properties")
    minecraft = raw.get("minecraft_version") or props.get("minecraft_version", "")
    if not minecraft:
        raise SystemExit(f"{path}: no minecraft_version, and none in gradle.properties")

    enum_java = raw.get("enum_java")
    return Config(
        repo=repo,
        mod_id=mod_id,
        name=raw.get("name") or props.get("mod_name", mod_id),
        version=raw.get("version") or props.get("version", ""),
        minecraft=minecraft,
        footer=raw.get("footer", ""),
        prefix=raw.get("prefix", "potted_"),
        resources=[repo / r for r in raw.get("resources", ["common/src/main/resources"])],
        sources=list(raw.get("sources", [])),
        item_namespace=raw.get("item_namespace", "minecraft"),
        item_aliases=dict(raw.get("item_aliases", {})),
        # 0 means unset, so --columns > gallery.json > OVERVIEW_COLUMNS stays a plain
        # `or` chain rather than three-way None handling.
        columns=int(raw.get("columns", 0) or 0),
        # Insertion order from the JSON is the order they appear in the menu, so the
        # mod decides which store it leads with rather than this file deciding for it.
        downloads=dict(raw.get("downloads", {})),
        tints={k: list(v) for k, v in raw.get("tints", {}).items()},
        enum_java=(repo / enum_java) if enum_java else None,
        logo=(repo / raw["logo"]) if raw.get("logo") else None,
        banner=(repo / raw["banner"]) if raw.get("banner") else None,
        author=raw.get("author", ""),
        since_base=raw.get("since_base", ""),
        properties=props,
    )


PROPERTY_REF = re.compile(r"\$\{([A-Za-z0-9_.]+)\}")


def expand(pattern: str, properties: dict[str, str]) -> str:
    """Substitute `${some_property}` from gradle.properties into a source pattern.

    Lets a config pin a dependency jar to the version the build already declares --
    `**/notenoughpots-fabric-${notenoughpots_lib_version}.jar` -- instead of a wildcard
    that can also match a stale jar for a different version sitting in the same cache.
    """
    def replace(match: re.Match) -> str:
        value = properties.get(match.group(1))
        if value is None:
            raise SystemExit(f"{pattern}: no such gradle property {match.group(1)}")
        return value
    return PROPERTY_REF.sub(replace, pattern)


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------

def find_client_jar(version: str) -> Path | None:
    """A vanilla client jar, wherever this machine happens to keep one."""
    home = Path.home()
    appdata = os.environ.get("APPDATA", "")
    candidates = [
        home / ".gradle/caches/fabric-loom" / version / "minecraft-client.jar",
        home / ".gradle/caches/fabric-loom" / version / "minecraft-client-only.jar",
        home / ".gradle/caches/fabric-loom" / version / "minecraft-merged.jar",
        Path(appdata) / ".minecraft/versions" / version / f"{version}.jar" if appdata else None,
        home / ".minecraft/versions" / version / f"{version}.jar",
        home / "Library/Application Support/minecraft/versions" / version / f"{version}.jar",
    ]
    return next((c for c in candidates if c and c.is_file()), None)


def collect_sources(config: Config, extra: list[str], jar: str | None) -> list[Source]:
    """Mod resources first, then the client jar, then everything else, in order given.

    Patterns are globbed, and anything that resolves to an empty or texture-less jar is
    dropped with a warning -- a build cache can hold a 0 KB stale artifact for the same
    coordinates as the real one, and picking it silently renders a model as nothing.
    """
    paths: list[Path] = [r for r in config.resources if r.is_dir()]
    missing = [r for r in config.resources if not r.is_dir()]
    for absent in missing:
        print(f"  ! resource directory not found: {absent}", file=sys.stderr)

    client = Path(jar).expanduser() if jar else find_client_jar(config.minecraft)
    if client is None:
        raise SystemExit(
            f"no vanilla {config.minecraft} client jar found. Run the mod's Gradle build "
            f"once, or pass --jar <path>."
        )
    paths.append(client)

    for raw in [*config.sources, *extra]:
        pattern = expand(raw, config.properties)
        candidate = Path(pattern).expanduser()
        hits = [candidate] if candidate.exists() else sorted(
            Path(p) for p in glob.glob(pattern, recursive=True)
        ) or sorted(config.repo.glob(pattern))
        if not hits:
            print(f"  ! no source matched: {pattern}", file=sys.stderr)
        paths += hits

    sources: list[Source] = []
    for path in paths:
        if path.is_file() and path.stat().st_size == 0:
            print(f"  ! ignoring empty jar: {path}", file=sys.stderr)
            continue
        try:
            sources.append(Source(path))
        except zipfile.BadZipFile:
            print(f"  ! not a readable jar: {path}", file=sys.stderr)
    return sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path("gallery.json"),
                        help="the mod's gallery.json (default: ./gallery.json)")
    parser.add_argument("--out", type=Path, help="output directory (default <repo>/build/gallery)")
    parser.add_argument("--jar", help="vanilla client jar to take textures from")
    parser.add_argument("--source", action="append", default=[], metavar="PATH",
                        help="extra resource directory or jar; repeatable, globs allowed")
    parser.add_argument("--size", type=int, default=256, help="pixels per render (default 256)")
    parser.add_argument("--overview-size", type=int, default=128, metavar="N",
                        help="pixels per overview tile (default 128); the sheet is a contact "
                             "sheet, so it does not want the cards' resolution")
    parser.add_argument("--ssaa", type=int, default=4, help="supersampling factor (default 4)")
    parser.add_argument("--columns", type=int, default=0, metavar="N",
                        help=f"overview columns; overrides gallery.json's \"columns\" "
                             f"(default {OVERVIEW_COLUMNS})")
    parser.add_argument("--theme", choices=sorted(THEMES), default="dark")
    parser.add_argument("--biome", choices=sorted(BIOMES), default="plains",
                        help="which biome colours the grass and foliage tints")
    parser.add_argument("--pitch", type=float, default=GUI_PITCH)
    parser.add_argument("--yaw", type=float, default=BLOCK_YAW)
    parser.add_argument("--only", help="regex, render just the variants it matches")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero if any texture or item icon is unresolved")
    parser.add_argument("--no-overview", action="store_true")
    parser.add_argument("--no-html", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.config.resolve())
    out = args.out or config.repo / "build" / "gallery"
    columns = max(1, args.columns or config.columns or OVERVIEW_COLUMNS)
    sources = collect_sources(config, args.source, args.jar)

    print(f"{config.name} {config.version} for Minecraft {config.minecraft}")
    for source in sources:
        print(f"  source    {source}")
    print(f"  output    {out}")

    assets = Assets(sources)
    temperature, downfall = BIOMES[args.biome]
    grass = colormap_color(assets, "grass", temperature, downfall)
    foliage = colormap_color(assets, "foliage", temperature, downfall)

    variants = read_catalogue(assets, config)
    if args.only:
        pattern = re.compile(args.only)
        variants = [v for v in variants if pattern.search(v.name)]
    if not variants:
        raise SystemExit(f"no blockstates under assets/{config.mod_id}/blockstates")

    models, tints, item_defs = {}, {}, {}
    for variant in variants:
        models[variant.name] = load_model(assets, variant.model)
        item_defs[variant.name] = item_definition(assets, config.item_namespace, variant.item)
        tints[variant.name] = tint_layers(assets, variant, item_defs[variant.name],
                                          grass, foliage, config.tints)

    quads = {v.name: build_quads(models[v.name], tints[v.name]) for v in variants}
    blank = [v.name for v in variants if not quads[v.name]]
    absent = sorted({q.texture for qs in quads.values() for q in qs
                     if assets.texture(q.texture) is None})
    frame = union([measure(q, args.pitch, args.yaw) for q in quads.values() if q])

    (out / "blocks").mkdir(parents=True, exist_ok=True)
    (out / "items").mkdir(parents=True, exist_ok=True)

    blocks, items, no_icon = {}, {}, []
    for index, variant in enumerate(variants, 1):
        print(f"\r  rendering {index}/{len(variants)} {variant.name:<34}", end="", flush=True)
        image = render(assets, quads[variant.name], frame, args.size, args.ssaa,
                       args.pitch, args.yaw)
        image.save(out / "blocks" / f"{variant.name}.png")
        blocks[variant.name] = image

        icon = render_item(assets, item_defs[variant.name], args.size, args.ssaa,
                           grass, foliage)
        if icon is None:
            no_icon.append(variant)
        else:
            icon.save(out / "items" / f"{variant.name}.png")
            items[variant.name] = icon
    print()

    manifest = {
        "mod": {"id": config.mod_id, "name": config.name,
                "version": config.version, "minecraft": config.minecraft},
        "biome": args.biome,
        # The page opens its 3D view on exactly this camera. It used to hardcode the same
        # two numbers in JavaScript, which is how they would silently drift apart.
        "view": {"pitch": args.pitch, "yaw": args.yaw},
        "variants": [
            {"name": v.name, "label": v.label, "source": v.source, "item": v.item,
             "model": v.model, "since": v.since,
             "tints": [f"#{t:06x}" for t in tints[v.name] if t >= 0],
             "block_png": f"blocks/{v.name}.png",
             "item_png": f"items/{v.name}.png" if v.name in items else None}
            for v in variants
        ],
    }
    (out / "gallery.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    web_facts = [p for p in (f"v{config.version}" if config.version else "",
                             f"Minecraft {config.minecraft}" if config.minecraft else "") if p]
    # Interleaved with the page's own divider, which only shows once the facts are
    # on one line -- beside the wordmark they stack, and a dot between stacked rows
    # would be a bullet. CSS decides which, so the markup carries both cases.
    web_dot = '<span class="dot" aria-hidden="true">&middot;</span>'
    web_subheading = web_dot.join(f'<span>{fact}</span>' for fact in web_facts)
    if not args.no_overview:
        font = McFont(assets)
        # The sheet composites at its own tile size. Downscaling the finished renders is
        # cheaper than rendering twice, and a 2:1 box reduction of a supersampled image is
        # clean -- the texels stay square because they were never resampled on the way in.
        sheet_art = frame_size(frame, args.overview_size)
        fit = lambda im, box: im if im.size == box else im.resize(box, BOX)
        sheet_blocks = {k: fit(v, sheet_art) for k, v in blocks.items()}
        # The item icons go in at full size: the slot reduces them in one step, and a
        # single box reduction beats two stacked ones.
        sheet = build_overview(variants, sheet_blocks, items, font, THEMES[args.theme],
                               columns, sheet_art, config.name,
                               f"v{config.version}" if config.version else "",
                               f"Minecraft {config.minecraft}" if config.minecraft else "",
                               config.footer, newest=config.minecraft)
        sheet.convert("RGB").save(out / "overview.png")
        print(f"  overview  {sheet.width}x{sheet.height}")
    if not args.no_html:
        # The wordmark, when the mod has one, and the icon either way -- the icon is
        # still the favicon even when the header shows the banner.
        banner_name = None
        source = find_banner(config)
        if source is not None:
            art = Image.open(source).convert("RGBA")
            art = crop_to_ink(art)
            art.thumbnail((640, 640), BOX)
            art.save(out / "banner.png")
            banner_name = "banner.png"
            print(f"  banner    {source.name} {art.width}x{art.height}")

        logo_name = None
        source = find_logo(config)
        if source is not None:
            mark = Image.open(source).convert("RGBA")
            # A favicon and an apple-touch-icon, both from the mod's own square icon.
            # No .ico is needed: every browser still in use takes a PNG icon.
            mark.resize((32, 32), BOX).save(out / "favicon.png")
            round_corners(Image.open(out / "favicon.png")).save(out / "favicon.png")
            mark.resize((180, 180), BOX).save(out / "apple-touch-icon.png")
            if banner_name is None:
                square = mark.copy()
                square.thumbnail((256, 256), BOX)
                square.save(out / "logo.png")
                logo_name = "logo.png"
            print(f"  icon      {source.name} -> favicon 32, touch 180"
                  + ("" if banner_name else ", header 256"))
        count = export_geometry(assets, quads, out)
        print(f"  3d view   {sum(len(q) for q in quads.values())} quads, {count} textures")
        tile = frame_size(frame, args.size)
        write_html(out / "index.html", f"{config.name} Gallery", config.name,
                   web_subheading, manifest, tile[0] / tile[1], logo=logo_name,
                   banner=banner_name, icon=source is not None,
                   footer=footer_markup(config.author, config.footer),
                   downloads=downloads_markup(config.downloads))

    for name in blank:
        print(f"  ! {name} resolved to a model with no elements", file=sys.stderr)
    for texture in absent:
        print(f"  ! texture not found: {texture}", file=sys.stderr)
    if no_icon:
        print("  ! no item icon for " + ", ".join(f"{v.name} ({v.item})" for v in no_icon),
              file=sys.stderr)

    print(f"  done, {len(variants)} variants in {out}")
    if args.strict and (blank or absent or no_icon):
        print("  strict: unresolved assets above", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
