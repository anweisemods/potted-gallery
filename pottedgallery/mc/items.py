"""The inventory icon of the item you pot a block with."""

from __future__ import annotations

import numpy as np
from PIL import Image

from .assets import Assets
from .colors import Palette, decode_tints, find_model_node
from .constants import BLOCK, GUI_PITCH, GUI_SCALE, GUI_YAW
from .model import build_quads, load_model
from ..imaging import NEAREST
from .raster import render, square_frame


def _flat_icon(assets: Assets, model, tints: list[int], size: int) -> Image.Image | None:
    """`item/generated`: a stack of flat sprites, drawn without any diffuse shading."""
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


def render_item(assets: Assets, item_def: dict | None, size: int, ssaa: int,
                palette: Palette) -> Image.Image | None:
    """Draw an item the way an inventory slot or an item frame would."""
    node = find_model_node(item_def) if item_def else None
    if node is None:
        return None
    model = load_model(assets, node["model"])
    tints = decode_tints(node.get("tints", []), palette)

    if not model.elements:
        return _flat_icon(assets, model, tints, size)

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
