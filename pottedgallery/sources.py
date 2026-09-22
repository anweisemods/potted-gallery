"""Where to look for a given mod's assets.

`mc.assets` knows how to read a source; this knows which ones a `gallery.json` means,
which is the one place the renderer has to care about Gradle, Loom and this machine's
caches.
"""

from __future__ import annotations

import glob
import os
import sys
import zipfile
from pathlib import Path

from .config import Config, expand
from .mc.assets import Source


def find_client_jar(version: str) -> Path | None:
    """A vanilla client jar, wherever this machine happens to keep one."""
    home = Path.home()
    appdata = os.environ.get("APPDATA", "")
    candidates = [
        home / ".gradle/caches/fabric-loom" / version / "minecraft-client.jar",
        home / ".gradle/caches/fabric-loom" / version / "minecraft-client-only.jar",
        home / ".gradle/caches/fabric-loom" / version / "minecraft-merged.jar",
        Path(appdata) / ".minecraft/versions" / version / f"{version}.jar" if appdata else None,
        home / ".minecraft/versions" / version / f"{version}.jar",
        home / "Library/Application Support/minecraft/versions" / version / f"{version}.jar",
    ]
    return next((c for c in candidates if c and c.is_file()), None)


def _matches(pattern: str, repo: Path) -> list[Path]:
    """Every path a source pattern resolves to, absolute or relative to the mod repo."""
    candidate = Path(pattern).expanduser()
    if candidate.exists():
        return [candidate]
    hits = sorted(Path(p) for p in glob.glob(pattern, recursive=True))
    if hits or candidate.is_absolute():
        # `Path.glob` raises NotImplementedError on an absolute pattern, so an absolute
        # one that matched nothing has to stop here rather than be retried under the repo.
        return hits
    return sorted(repo.glob(pattern))


def collect_sources(config: Config, extra: list[str], jar: str | None) -> list[Source]:
    """Mod resources first, then the client jar, then everything else, in order given.

    Patterns are globbed, and anything that resolves to an empty or texture-less jar is
    dropped with a warning -- a build cache can hold a 0 KB stale artifact for the same
    coordinates as the real one, and picking it silently renders a model as nothing.
    """
    paths: list[Path] = [r for r in config.resources if r.is_dir()]
    for absent in (r for r in config.resources if not r.is_dir()):
        print(f"  ! resource directory not found: {absent}", file=sys.stderr)

    client = Path(jar).expanduser() if jar else find_client_jar(config.minecraft)
    if client is None:
        raise SystemExit(
            f"no vanilla {config.minecraft} client jar found. Run the mod's Gradle build "
            f"once, or pass --jar <path>."
        )
    paths.append(client)

    for raw in [*config.sources, *extra]:
        pattern = expand(raw, config.properties)
        hits = _matches(pattern, config.repo)
        if not hits:
            print(f"  ! no source matched: {pattern}", file=sys.stderr)
        paths += hits

    sources: list[Source] = []
    for path in paths:
        if path.is_file() and path.stat().st_size == 0:
            print(f"  ! ignoring empty jar: {path}", file=sys.stderr)
            continue
        try:
            sources.append(Source(path))
        except zipfile.BadZipFile:
            print(f"  ! not a readable jar: {path}", file=sys.stderr)
    return sources
