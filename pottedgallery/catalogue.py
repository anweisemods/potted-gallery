"""What the mod actually adds: one `Variant` per potted block, in the order the gallery
shows them.

The catalogue comes from `assets/<mod_id>/blockstates`, not from Java. An optional enum
only adds the source block, the version badges and a nicer ordering.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .mc.assets import Assets

# `POTTED_GLOW_LICHEN(Blocks.GLOW_LICHEN, true)` has to match as readily as the one
# argument form: an entry that fails to parse is not an error, it silently falls back to
# `since_base` and sorts last, which reads as a wrong badge rather than as a parse miss.
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


@dataclass
class Enum:
    """What an optional Java enum contributes: source blocks, versions and an order."""

    blocks: dict[str, str]
    since: dict[str, str]
    order: list[str]

    @classmethod
    def empty(cls) -> Enum:
        return cls({}, {}, [])


def read_enum(path: Path, base: str) -> Enum:
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
    return Enum(blocks, since, order)


def read_catalogue(assets: Assets, config: Config) -> list[Variant]:
    enum = read_enum(config.enum_java, config.since_base) if config.enum_java else Enum.empty()

    names = assets.stems(config.mod_id, "blockstates")
    # Newest Minecraft version first, and inside a version the enum's own declaration
    # order -- which groups related blocks (all the corals, all the leaves) far better
    # than alphabetical does. A mod with no enum has no version data at all, so `ranked`
    # and `since` are both empty and this degrades to plain alphabetical.
    ranked = {name: i for i, name in enumerate(enum.order)}
    names.sort(key=lambda n: (version_key(enum.since.get(n, "")),
                              ranked.get(n, len(ranked)), n))

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
        block = enum.blocks.get(name, plain)
        variants.append(Variant(
            name=name,
            source=block,
            item=config.item_aliases.get(block, block),
            model=state["model"],
            label=plain.replace("_", " ").title(),
            since=enum.since.get(name, config.since_base),
        ))
    return variants
