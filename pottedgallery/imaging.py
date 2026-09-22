"""Pillow resampling filters, named once.

`Image.Resampling` arrived in Pillow 9.1, which is the floor `requirements.txt` states;
the fallback is there so an older Pillow degrades to the deprecated top-level names
rather than raising on import.
"""

from __future__ import annotations

from PIL import Image

NEAREST = getattr(Image, "Resampling", Image).NEAREST
BOX = getattr(Image, "Resampling", Image).BOX
