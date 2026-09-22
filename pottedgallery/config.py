"""The `gallery.json` a mod describes itself with, and the `gradle.properties` behind it."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


def read_properties(path: Path) -> dict[str, str]:
    props = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()
    return props


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
    author_url: str = ""
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
        author_url=raw.get("author_url", ""),
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
