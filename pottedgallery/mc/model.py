"""Model JSON in, quads out.

Resolves a model's parent chain and its `#texture` indirections, then bakes each face
into a quad the way Minecraft's own FaceBakery does -- including element rotations,
`rescale`, face UV rotation and the fixed per-direction shade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .assets import Assets
from .constants import BLOCK, NORMALS, RESCALE, SHADE


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


def _element_transform(rot: dict | None):
    """The rotation an element declares, as (matrix, origin, per-axis scale)."""
    if not rot:
        return None, None, None
    matrix = rotation_matrix(rot["axis"], float(rot["angle"]))
    origin = np.array(rot["origin"], dtype=np.float64)
    scale = np.ones(3)
    if rot.get("rescale"):
        factor = RESCALE.get(abs(float(rot["angle"])), 1.0)
        for i, ax in enumerate("xyz"):
            if ax != rot["axis"]:
                scale[i] = factor
    return matrix, origin, scale


def _face_quad(face: str, spec: dict, texture: str, f: list[float], t: list[float],
               transform, tints: list) -> Quad:
    matrix, origin, scale = transform
    # The face always covers the element's own rectangle. A model that states a
    # `uv` is choosing a different slice of the texture to stretch over it, not
    # moving the geometry -- so positions come from the default UV and the stated
    # one only drives sampling.
    du0, dv0, du1, dv1 = default_uv(face, f, t)
    u0, v0, u1, v1 = spec.get("uv") or (du0, dv0, du1, dv1)
    turns = (int(spec.get("rotation", 0)) // 90) % 4

    pos, uv = [], []
    for s, q in ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)):
        pos.append(face_corner(face, du0 + (du1 - du0) * s, dv0 + (dv1 - dv0) * q, f, t))
        rs, rq = s, q
        for _ in range(turns):  # a face rotation turns the texture, not the quad
            rs, rq = rq, 1.0 - rs
        uv.append((u0 + (u1 - u0) * rs, v0 + (v1 - v0) * rq))

    points = np.array(pos, dtype=np.float64)
    normal = np.array(NORMALS[face], dtype=np.float64)
    if matrix is not None:
        points = (matrix @ ((points - origin) * scale).T).T + origin
        normal = matrix @ normal

    tint_index = spec.get("tintindex", -1)
    return Quad(
        pos=points,
        uv=np.array(uv, dtype=np.float64),
        normal=normal,
        texture=texture,
        shade=1.0,  # the caller applies the element's shade, which needs the normal
        tint=tints[tint_index] if 0 <= tint_index < len(tints) else -1,
    )


def build_quads(model: Model, tints: list) -> list[Quad]:
    quads: list[Quad] = []
    for element in model.elements:
        f, t = [float(x) for x in element["from"]], [float(x) for x in element["to"]]
        unlit = element.get("light_emission", 0) >= 15
        shaded = element.get("shade", True) and not unlit
        transform = _element_transform(element.get("rotation"))

        for face, spec in element.get("faces", {}).items():
            texture = model.resolve(spec.get("texture", ""))
            if texture is None:
                continue
            quad = _face_quad(face, spec, texture, f, t, transform, tints)
            if shaded:
                # Shading follows the rotated normal, the way FaceBakery re-derives a
                # quad's direction after rotating it -- a 45 degree cross plane is lit
                # as north/south.
                axis = max(NORMALS, key=lambda d: float(np.dot(quad.normal, NORMALS[d])))
                quad.shade = SHADE[axis]
            quads.append(quad)
    return quads
