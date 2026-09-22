"""`overview.png`, the contact sheet.

Drawn the way Minecraft draws a GUI: square corners, hard one-pixel borders, bevelled
inventory slots, and the game's own font. Every measurement is a multiple of `u`, one
Minecraft pixel at the tile size the sheet composites at, so the layout sits on the same
grid as the art rather than beside it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw

from .catalogue import Variant
from .imaging import BOX
from .mc.font import McFont

# Wide enough that a hundred variants do not turn the sheet into a 1:5 ribbon, narrow
# enough that a card still has room for its label. A mod overrides it with "columns" in
# its gallery.json, which is what the workflow passes along, and --columns beats both.
OVERVIEW_COLUMNS = 6

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


# --------------------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Scale:
    """The sheet's whole measurement system, in multiples of one Minecraft pixel."""

    u: int              # one Minecraft pixel at the tile size the sheet composites at
    body: int           # McFont scales by whole numbers, so body and title are the choice
    tag: int
    title: int
    line_h: int         # leading, so wrapped labels are not set solid
    rule: int           # one weight of stroke on the whole sheet
    pad: int            # a card's inner padding, all four sides
    gutter: int         # between two cards
    margin: int         # around the whole sheet
    step: int           # between a card's three bands
    split: int          # between the masthead, the grid and the footer
    slot: int           # the game's own slot: 16 for the item, 1 each side
    icon: int

    @classmethod
    def of(cls, font: McFont, art: tuple[int, int]) -> Scale:
        u = max(1, max(art) // 64)
        # McFont only scales by whole numbers, so the tag reads at the body size or at
        # half it, with nothing in between. Half was too quiet for the one fact a card
        # carries besides its name.
        return cls(u=u, body=u, tag=u, title=u * 2,
                   line_h=font.height(u) + u * 2, rule=u, pad=u * 8, gutter=u * 4,
                   margin=u * 24, step=u * 6, split=u * 20, slot=u * 18, icon=u * 16)


@dataclass(frozen=True)
class CardLayout:
    """How big a card is, and how much of it each band takes."""

    labels: dict[str, list[str]]
    rows_of_text: int
    width: int
    height: int
    meta_h: int
    label_h: int


def _plan_cards(variants: list[Variant], font: McFont, s: Scale,
                art: tuple[int, int], has_slot: bool) -> CardLayout:
    art_w, art_h = art
    # Wrap first, then size the card to what the text actually needs, so no label can
    # overflow into its neighbour however long a block's name turns out to be.
    labels = {v.name: font.wrap(v.label, s.body, art_w, lines=3) for v in variants}
    rows_of_text = max(len(lines) for lines in labels.values())
    widest_label = max(font.width(line, s.body) for lines in labels.values() for line in lines)
    widest_tag = max((font.width(v.since, s.tag) for v in variants if v.since), default=0)

    meta_h = max(font.height(s.tag) if widest_tag else 0, s.slot if has_slot else 0)
    # The tag and the slot sit at opposite ends of the meta band and must never meet.
    meta_w = widest_tag + (s.gutter + s.slot if has_slot else 0)
    width = max(art_w, widest_label, meta_w) + s.pad * 2
    # The last line carries no trailing leading, so the padding under the label measures
    # the same as the padding over the meta band.
    label_h = s.line_h * rows_of_text - (s.line_h - font.height(s.body))
    height = s.pad + (meta_h + s.step if meta_h else 0) + art_h + s.step + label_h + s.pad
    return CardLayout(labels, rows_of_text, width, height, meta_h, label_h)


def _draw_masthead(sheet: Image.Image, font: McFont, s: Scale, theme: dict,
                   title: str, version: str, minecraft: str, total: str, unit: str,
                   right: int, head_y: int, cap_y: int) -> None:
    """Two columns of the same shape: a value at the heading size over the thing that
    qualifies it at the caption size.

    Left, the mod's name over its versions; right, how many blocks are on the sheet over
    the word for them. Two type sizes and two colours for the whole band, which is what
    holds the three facts together -- anything set in `text` is a value, anything in
    `muted` says what that value is.
    """
    shadow = theme["shadow"]
    font.draw(sheet, (s.margin, head_y), title, s.title, theme["text"], shadow)
    font.draw(sheet, (right - font.width(total, s.title), head_y), total, s.title,
              theme["text"], shadow)

    # The versions share one line because the pair is a single fact: this build of the
    # mod, for that build of the game. The mod's own version leads in `text` because it
    # qualifies the name directly.
    x = s.margin
    if version:
        font.draw(sheet, (x, cap_y), version, s.body, theme["text"], shadow)
        x += font.width(version, s.body) + (s.u * 3 if minecraft else 0)
    if minecraft:
        font.draw(sheet, (x, cap_y), minecraft, s.body, theme["muted"], shadow)
    font.draw(sheet, (right - font.width(unit, s.body), cap_y), unit, s.body,
              theme["muted"], shadow)


def _draw_card(sheet: Image.Image, draw, variant: Variant, box: Box, font: McFont,
               s: Scale, theme: dict, card: CardLayout, art: tuple[int, int],
               block: Image.Image | None, icon: Image.Image | None, newest: str) -> None:
    art_w, art_h = art
    x, y, _, _ = box
    shadow = theme["shadow"]
    panel(draw, box, theme["tile"], theme["edge"], s.rule)

    # The meta band is the card's header: which version the pot arrived in on the
    # left, what you pot it with on the right. Keeping the slot up here is what stops
    # it landing on top of a tall plant, which is where it used to sit.
    left, right = x + s.pad, x + card.width - s.pad
    if variant.since:
        tag_y = y + s.pad + (card.meta_h - font.height(s.tag)) // 2
        font.draw(sheet, (left, tag_y), variant.since, s.tag,
                  theme["accent"] if variant.since == newest else theme["muted"], shadow)

    if icon is not None:
        slot_y = y + s.pad + (card.meta_h - s.slot) // 2
        inventory_slot(draw, (right - s.slot, slot_y, right, slot_y + s.slot), theme, s.rule)
        sheet.alpha_composite(icon.resize((s.icon, s.icon), BOX),
                              (right - s.slot + s.u, slot_y + s.u))

    art_y = y + s.pad + (card.meta_h + s.step if card.meta_h else 0)
    if block is not None:
        sheet.alpha_composite(block, (x + (card.width - art_w) // 2, art_y))

    # Every card reserves room for the longest name on the sheet, and the label packs
    # to the bottom of it. So a one-line name sits on the same baseline as the last
    # line of a three-line one, and the slack it does not use lands above the label,
    # beside the artwork, where it reads as breathing room instead of a ragged edge.
    lines = card.labels[variant.name]
    text_y = art_y + art_h + s.step + s.line_h * (card.rows_of_text - len(lines))
    for line in lines:
        font.draw(sheet, (x + (card.width - font.width(line, s.body)) // 2, text_y),
                  line, s.body, theme["text"], shadow)
        text_y += s.line_h


def build_overview(variants: list[Variant], blocks: dict[str, Image.Image],
                   items: dict[str, Image.Image], font: McFont, theme: dict,
                   columns: int, art: tuple[int, int], title: str, version: str,
                   minecraft: str, footer: str, newest: str = "") -> Image.Image:
    s = Scale.of(font, art)
    card = _plan_cards(variants, font, s, art, has_slot=bool(items))

    rows = math.ceil(len(variants) / columns)
    grid_w = columns * card.width + (columns - 1) * s.gutter
    grid_h = rows * card.height + (rows - 1) * s.gutter

    total, unit = str(len(variants)), "variants"
    versions_w = font.width(version, s.body) + font.width(minecraft, s.body) \
        + (s.u * 3 if version and minecraft else 0)
    # The two masthead columns must not meet, however narrow the grid is. A mod asking
    # for three columns has a grid narrower than its own name set at the heading size.
    apart = s.u * 16
    content_w = max(grid_w,
                    font.width(title, s.title) + apart + font.width(total, s.title),
                    versions_w + apart + font.width(unit, s.body))
    width = content_w + s.margin * 2
    grid_x = (width - grid_w) // 2          # centred, so a widened sheet stays balanced
    right = width - s.margin

    head_y = s.margin
    cap_y = head_y + font.height(s.title) + s.u * 4
    # No rule above the grid or below it: the cards already draw a hard top and bottom
    # edge across the full span, so a second line beside them was drawing the same border
    # twice. White space separates the three bands instead.
    grid_y = cap_y + font.height(s.body) + s.split
    foot_y = grid_y + grid_h + s.split
    height = foot_y + font.height(s.body) + s.margin

    sheet = Image.new("RGBA", (width, height), theme["bg"] + (255,))
    draw = ImageDraw.Draw(sheet)

    _draw_masthead(sheet, font, s, theme, title, version, minecraft, total, unit,
                   right, head_y, cap_y)

    for index, variant in enumerate(variants):
        row, col = divmod(index, columns)
        x = grid_x + col * (card.width + s.gutter)
        y = grid_y + row * (card.height + s.gutter)
        _draw_card(sheet, draw, variant, (x, y, x + card.width, y + card.height),
                   font, s, theme, card, art, blocks.get(variant.name),
                   items.get(variant.name), newest)

    font.draw(sheet, (s.margin, foot_y), footer, s.body, theme["muted"], theme["shadow"])
    return sheet
