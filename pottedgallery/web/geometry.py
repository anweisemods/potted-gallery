"""Geometry export, for the turntable in the web gallery.

The page draws the same quads the PNGs are rasterised from, so the 3D view and the
thumbnail it opens from cannot disagree about a model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from PIL import Image

from ..mc.assets import Assets
from ..mc.model import Quad
from ..mc.raster import texel_rect


def texture_filename(rid: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", rid.lower()).strip("_") + ".png"


def _quad_entry(quad: Quad, tex: np.ndarray, index: int, blend: bool) -> dict:
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
        "i": index,
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
    if blend:
        entry["b"] = 1
    return entry


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
            exported.append(_quad_entry(quad, tex, textures.index(file),
                                        assets.translucent(quad.texture)))
        models[name] = {"t": textures, "q": exported}

    payload = json.dumps(models, separators=(",", ":"))
    (out / "models.js").write_text(f"window.NEP_MODELS = {payload};\n", encoding="utf-8")
    return len(written)
