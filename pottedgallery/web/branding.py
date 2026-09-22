"""The mod's own artwork: its wordmark, its icon, and the favicons cut from them."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..config import Config
from ..imaging import BOX


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


@dataclass
class Branding:
    """What the page ends up referencing, once the artwork has been written out."""

    banner: str | None = None   # the wordmark, shown in place of the icon and the name
    logo: str | None = None     # the square icon, shown only when there is no wordmark
    icons: bool = False         # whether favicon.png / apple-touch-icon.png were written


def export(config: Config, out: Path) -> Branding:
    """Write the header art and the favicons, and say what the page may reference.

    The icon is the favicon even when the header shows the wordmark, which is why both
    are looked for and only the header mark is exclusive.
    """
    result = Branding()

    source = find_banner(config)
    if source is not None:
        art = crop_to_ink(Image.open(source).convert("RGBA"))
        art.thumbnail((640, 640), BOX)
        art.save(out / "banner.png")
        result.banner = "banner.png"
        print(f"  banner    {source.name} {art.width}x{art.height}")

    source = find_logo(config)
    if source is not None:
        mark = Image.open(source).convert("RGBA")
        # A favicon and an apple-touch-icon, both from the mod's own square icon.
        # No .ico is needed: every browser still in use takes a PNG icon.
        round_corners(mark.resize((32, 32), BOX)).save(out / "favicon.png")
        mark.resize((180, 180), BOX).save(out / "apple-touch-icon.png")
        result.icons = True
        if result.banner is None:
            square = mark.copy()
            square.thumbnail((256, 256), BOX)
            square.save(out / "logo.png")
            result.logo = "logo.png"
        print(f"  icon      {source.name} -> favicon 32, touch 180"
              + ("" if result.banner else ", header 256"))
    return result
