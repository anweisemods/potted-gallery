"""Command line entry point, and the order the gallery is built in."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .catalogue import Variant, read_catalogue
from .config import Config, load_config
from .imaging import BOX
from .mc.assets import Assets
from .mc.colors import BIOMES, Palette, item_definition, tint_layers
from .mc.constants import BLOCK_YAW, GUI_PITCH
from .mc.font import McFont
from .mc.items import render_item
from .mc.model import Quad, build_quads, load_model
from .mc.raster import Frame, frame_size, measure, render, union
from .sheet import OVERVIEW_COLUMNS, THEMES, build_overview
from .sources import collect_sources
from .web import branding, page
from .web.geometry import export_geometry

DESCRIPTION = ("Render a gallery of a flower-pot mod's blocks, plus the item you pot "
               "each one with.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pottedgallery", description=DESCRIPTION)
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
    return parser


# --------------------------------------------------------------------------------------
# The build, in four steps: resolve, rasterise, write, report
# --------------------------------------------------------------------------------------

@dataclass
class Resolved:
    """Everything the catalogue turns into before a single pixel is drawn."""

    variants: list[Variant]
    item_defs: dict[str, dict | None]
    tints: dict[str, list[int]]
    quads: dict[str, list[Quad]]
    frame: Frame


@dataclass
class Rendered:
    """The images, kept in memory because the contact sheet is built from them."""

    blocks: dict[str, Image.Image] = field(default_factory=dict)
    items: dict[str, Image.Image] = field(default_factory=dict)
    no_icon: list[Variant] = field(default_factory=list)


def resolve(assets: Assets, config: Config, palette: Palette, variants: list[Variant],
            pitch: float, yaw: float) -> Resolved:
    item_defs, tints, quads = {}, {}, {}
    for variant in variants:
        item_defs[variant.name] = item_definition(assets, config.item_namespace, variant.item)
        tints[variant.name] = tint_layers(variant.source, item_defs[variant.name],
                                          palette, config.tints)
        quads[variant.name] = build_quads(load_model(assets, variant.model),
                                          tints[variant.name])
    frame = union([measure(q, pitch, yaw) for q in quads.values() if q])
    return Resolved(variants, item_defs, tints, quads, frame)


def rasterise(assets: Assets, out: Path, resolved: Resolved, palette: Palette,
              size: int, ssaa: int, pitch: float, yaw: float) -> Rendered:
    (out / "blocks").mkdir(parents=True, exist_ok=True)
    (out / "items").mkdir(parents=True, exist_ok=True)

    result = Rendered()
    total = len(resolved.variants)
    for index, variant in enumerate(resolved.variants, 1):
        print(f"\r  rendering {index}/{total} {variant.name:<34}", end="", flush=True)
        image = render(assets, resolved.quads[variant.name], resolved.frame,
                       size, ssaa, pitch, yaw)
        image.save(out / "blocks" / f"{variant.name}.png")
        result.blocks[variant.name] = image

        icon = render_item(assets, resolved.item_defs[variant.name], size, ssaa, palette)
        if icon is None:
            result.no_icon.append(variant)
        else:
            icon.save(out / "items" / f"{variant.name}.png")
            result.items[variant.name] = icon
    print()
    return result


def build_manifest(config: Config, resolved: Resolved, rendered: Rendered,
                   biome: str, pitch: float, yaw: float) -> dict:
    return {
        "mod": {"id": config.mod_id, "name": config.name,
                "version": config.version, "minecraft": config.minecraft},
        "biome": biome,
        # The page opens its 3D view on exactly this camera. It used to hardcode the same
        # two numbers in JavaScript, which is how they would silently drift apart.
        "view": {"pitch": pitch, "yaw": yaw},
        "variants": [
            {"name": v.name, "label": v.label, "source": v.source, "item": v.item,
             "model": v.model, "since": v.since,
             "tints": [f"#{t:06x}" for t in resolved.tints[v.name] if t >= 0],
             "block_png": f"blocks/{v.name}.png",
             "item_png": f"items/{v.name}.png" if v.name in rendered.items else None}
            for v in resolved.variants
        ],
    }


def version_fact(config: Config) -> str:
    return f"v{config.version}" if config.version else ""


def minecraft_fact(config: Config) -> str:
    return f"Minecraft {config.minecraft}" if config.minecraft else ""


def facts(config: Config) -> list[str]:
    """The two things the masthead says about the build, in the order it says them.

    The sheet and the page set them differently -- the sheet draws its own separator in
    the game's font, the page interleaves a CSS-controlled divider -- so they share the
    strings and not the joining.
    """
    return [fact for fact in (version_fact(config), minecraft_fact(config)) if fact]


def write_overview(out: Path, config: Config, resolved: Resolved, rendered: Rendered,
                   font: McFont, theme: dict, columns: int, tile: int) -> tuple[int, int]:
    # The sheet composites at its own tile size. Downscaling the finished renders is
    # cheaper than rendering twice, and a 2:1 box reduction of a supersampled image is
    # clean -- the texels stay square because they were never resampled on the way in.
    art = frame_size(resolved.frame, tile)
    blocks = {name: image if image.size == art else image.resize(art, BOX)
              for name, image in rendered.blocks.items()}
    # The item icons go in at full size: the slot reduces them in one step, and a
    # single box reduction beats two stacked ones.
    sheet = build_overview(resolved.variants, blocks, rendered.items, font, theme,
                           columns, art, config.name, version_fact(config),
                           minecraft_fact(config), config.footer,
                           newest=config.minecraft)
    sheet.convert("RGB").save(out / "overview.png")
    return sheet.width, sheet.height


def write_web(out: Path, config: Config, assets: Assets, resolved: Resolved,
              manifest: dict, size: int) -> None:
    brand = branding.export(config, out)
    textures = export_geometry(assets, resolved.quads, out)
    print(f"  3d view   {sum(len(q) for q in resolved.quads.values())} quads, "
          f"{textures} textures")
    tile = frame_size(resolved.frame, size)
    page.write_html(
        out / "index.html",
        title=f"{config.name} Gallery",
        heading=config.name,
        subheading=page.subheading_markup(facts(config)),
        manifest=manifest,
        aspect=tile[0] / tile[1],
        logo=brand.logo,
        banner=brand.banner,
        icons=brand.icons,
        footer=page.footer_markup(config.author, config.footer, config.author_url),
        downloads=page.downloads_markup(config.downloads),
    )


def report(resolved: Resolved, rendered: Rendered, assets: Assets) -> bool:
    """Print everything that did not resolve. True if anything did not."""
    blank = [name for name, quads in resolved.quads.items() if not quads]
    absent = sorted({q.texture for quads in resolved.quads.values() for q in quads
                     if assets.texture(q.texture) is None})
    for name in blank:
        print(f"  ! {name} resolved to a model with no elements", file=sys.stderr)
    for texture in absent:
        print(f"  ! texture not found: {texture}", file=sys.stderr)
    if rendered.no_icon:
        print("  ! no item icon for "
              + ", ".join(f"{v.name} ({v.item})" for v in rendered.no_icon), file=sys.stderr)
    return bool(blank or absent or rendered.no_icon)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = load_config(args.config.resolve())
    out = args.out or config.repo / "build" / "gallery"
    columns = max(1, args.columns or config.columns or OVERVIEW_COLUMNS)
    assets = Assets(collect_sources(config, args.source, args.jar))
    try:
        print(f"{config.name} {config.version} for Minecraft {config.minecraft}")
        for source in assets.sources:
            print(f"  source    {source}")
        print(f"  output    {out}")

        variants = read_catalogue(assets, config)
        if args.only:
            pattern = re.compile(args.only)
            variants = [v for v in variants if pattern.search(v.name)]
        if not variants:
            raise SystemExit(f"no blockstates under assets/{config.mod_id}/blockstates")

        palette = Palette(assets, args.biome)
        resolved = resolve(assets, config, palette, variants, args.pitch, args.yaw)
        rendered = rasterise(assets, out, resolved, palette, args.size, args.ssaa,
                             args.pitch, args.yaw)

        manifest = build_manifest(config, resolved, rendered, args.biome,
                                  args.pitch, args.yaw)
        (out / "gallery.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        if not args.no_overview:
            width, height = write_overview(out, config, resolved, rendered,
                                           McFont(assets), THEMES[args.theme], columns,
                                           args.overview_size)
            print(f"  overview  {width}x{height}")
        if not args.no_html:
            write_web(out, config, assets, resolved, manifest, args.size)

        unresolved = report(resolved, rendered, assets)
        print(f"  done, {len(resolved.variants)} variants in {out}")
        if args.strict and unresolved:
            print("  strict: unresolved assets above", file=sys.stderr)
            return 1
        return 0
    finally:
        assets.close()


if __name__ == "__main__":
    sys.exit(main())
