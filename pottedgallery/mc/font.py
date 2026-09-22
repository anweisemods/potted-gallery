"""The Minecraft font, read out of the client jar."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from ..imaging import NEAREST
from .assets import Assets


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

    def wrap(self, text: str, scale: int, limit: int, lines: int = 2) -> list[str]:
        """Break `text` over at most `lines` lines, each no wider than `limit` if it can be."""
        out, current = [], ""
        for word in text.split(" "):
            candidate = f"{current} {word}".strip()
            if current and len(out) < lines - 1 and self.width(candidate, scale) > limit:
                out.append(current)
                current = word
            else:
                current = candidate
        out.append(current)
        return out
