"""Where the renderer gets its bytes from: resource directories, jars, and the textures
and JSON inside them.

Everything downstream asks `Assets` for a resource id and never learns whether it came
out of the mod's source tree, the vanilla client jar or a remapped dependency.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image


class Source:
    """One place to look for assets: a resource directory or a jar."""

    def __init__(self, path: Path):
        self.path = path
        self.zip = None if path.is_dir() else zipfile.ZipFile(path)
        self.entries = frozenset() if self.zip is None else frozenset(self.zip.namelist())

    def read(self, rel: str) -> bytes | None:
        if self.zip is None:
            candidate = self.path / rel
            return candidate.read_bytes() if candidate.is_file() else None
        return self.zip.read(rel) if rel in self.entries else None

    def stems(self, folder: str) -> list[str]:
        """Names of the .json files directly inside `folder`, without the extension."""
        if self.zip is None:
            return [p.stem for p in (self.path / folder).glob("*.json")]
        head = folder + "/"
        return [n[len(head):-len(".json")] for n in self.entries
                if n.startswith(head) and n.endswith(".json") and "/" not in n[len(head):]]

    def close(self) -> None:
        if self.zip is not None:
            self.zip.close()
            self.zip = None

    def __str__(self) -> str:
        return str(self.path)


def is_translucent(tex: np.ndarray) -> bool:
    """Since 26.1 the render layer is decided per quad from the texture's own transparency."""
    alpha = tex[..., 3]
    partial = np.count_nonzero((alpha > 0.03) & (alpha < 0.97))
    return partial > 0.01 * alpha.size


class Assets:
    """Resolves ``namespace:path`` ids against an ordered list of sources.

    A compat mod reaches across several at once: its own resources, the vanilla client
    jar, the mod it adds support for, and the mod it is an addon of -- PottedDelight's
    models inherit from `notenoughpots:block/template_potted_block` and paint themselves
    with `farmersdelight:` textures.
    """

    def __init__(self, sources: list[Source]):
        self.sources = sources
        self.origin: dict[str, Source] = {}
        self._textures: dict[str, np.ndarray | None] = {}
        self._translucent: dict[str, bool] = {}

    @staticmethod
    def split(rid: str, default_ns: str = "minecraft") -> tuple[str, str]:
        ns, _, path = rid.partition(":")
        return (ns, path) if path else (default_ns, ns)

    def read(self, ns: str, kind: str, path: str) -> bytes | None:
        rel = f"assets/{ns}/{kind}/{path}"
        for source in self.sources:
            data = source.read(rel)
            if data is not None:
                self.origin.setdefault(ns, source)
                return data
        return None

    def stems(self, ns: str, kind: str) -> list[str]:
        found: list[str] = []
        for source in self.sources:
            found += source.stems(f"assets/{ns}/{kind}")
        return sorted(dict.fromkeys(found))

    def json(self, ns: str, kind: str, path: str) -> dict | None:
        raw = self.read(ns, kind, path)
        return json.loads(raw) if raw is not None else None

    def texture(self, rid: str) -> np.ndarray | None:
        """A texture as float RGBA in 0..1. Animated textures collapse to their first frame."""
        if rid in self._textures:
            return self._textures[rid]
        ns, path = self.split(rid)
        raw = self.read(ns, "textures", f"{path}.png")
        out = None
        if raw is not None:
            img = Image.open(io.BytesIO(raw)).convert("RGBA")
            meta = self.read(ns, "textures", f"{path}.png.mcmeta")
            if meta is not None and b"animation" in meta and img.height > img.width:
                img = img.crop((0, 0, img.width, img.width))
            out = np.asarray(img, dtype=np.float32) / 255.0
        self._textures[rid] = out
        return out

    def translucent(self, rid: str) -> bool:
        """Whether a texture blends rather than going through the cutout alpha test.

        Memoised: a model asks per quad and the answer scans the whole texture, so the
        same six textures would otherwise be counted over and over for every variant.
        """
        if rid not in self._translucent:
            tex = self.texture(rid)
            self._translucent[rid] = tex is not None and is_translucent(tex)
        return self._translucent[rid]

    def close(self) -> None:
        for source in self.sources:
            source.close()
