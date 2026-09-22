"""Tints: the colours the client multiplies onto a model at runtime.

Some of them are stated in the assets (a vanilla item definition carries its own), some
come out of a colormap, and the rest live only in a mod's client class and have to be
restated in `gallery.json`.
"""

from __future__ import annotations

from .assets import Assets

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


class Palette:
    """The two biome-dependent colours, resolved once for the whole run."""

    def __init__(self, assets: Assets, biome: str):
        temperature, downfall = BIOMES[biome]
        self.biome = biome
        self.grass = colormap_color(assets, "grass", temperature, downfall)
        self.foliage = colormap_color(assets, "foliage", temperature, downfall)


# --------------------------------------------------------------------------------------
# Item definitions, which are where a vanilla tint is usually written down
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


# --------------------------------------------------------------------------------------
# Turning a declaration into a colour
# --------------------------------------------------------------------------------------

def decode_tints(specs: list, palette: Palette) -> list[int]:
    """The tint layers an asset states, as packed RGB."""
    out = []
    for spec in specs:
        kind = spec.get("type", "") if isinstance(spec, dict) else ""
        if kind.endswith(":constant"):
            out.append(int(spec.get("value", -1)) & 0xFFFFFF)
        elif kind.endswith(":grass"):
            out.append(palette.grass)
        elif kind.endswith(":foliage"):
            out.append(palette.foliage)
        else:
            out.append(-1)
    return out


def config_tint(spec, palette: Palette) -> int:
    """One entry of the config's `tints` table."""
    if spec == "grass":
        return palette.grass
    if spec == "foliage":
        return palette.foliage
    if isinstance(spec, dict) and "stem" in spec:
        return stem_color(int(spec["stem"]))
    if isinstance(spec, str):
        return int(spec[1:] if spec.startswith("#") else spec, 16)
    return int(spec)


def tint_layers(source: str, item_def: dict | None, palette: Palette,
                coded: dict) -> list[int]:
    """The colour of each ``tintindex`` layer, mirroring what the client registers.

    On 1.21.4+ most of it is already stated in the vanilla item definition (leaves carry
    a constant, the grasses carry ``minecraft:grass``). The rest lives only in the mod's
    client class, so it has to be restated in the config.
    """
    layers = coded.get(source)
    if layers is not None:
        return [config_tint(layer, palette) for layer in layers]
    node = find_model_node(item_def) if item_def else None
    return decode_tints(node.get("tints", []), palette) if node else []
