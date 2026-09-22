"""The handful of numbers Minecraft's own renderer is built on.

Kept in one place because several modules need them and every one of them is a fact
about the game rather than a choice this tool made.
"""

from __future__ import annotations

import math

# Block space is 0..16 on every axis; the centre of a block is (8, 8, 8).
BLOCK = 16.0

# The angle `minecraft:block/block` uses for its `gui` display transform. Every block you
# have ever seen in an inventory slot or an item frame is drawn from here.
#
# The model file writes the yaw as 225, but that is measured through Minecraft's own GUI
# matrix stack, which mirrors an axis on the way. 135 is the same view in the plain
# right-handed frame this renderer uses -- checked against the furnace, the crafting table
# and the stairs, whose inventory icons all pin down which two side faces you can see.
GUI_PITCH, GUI_YAW = 30.0, 135.0
GUI_SCALE = 0.625

# Item icons keep GUI_YAW, because that is literally what an item frame shows. The potted
# blocks get the mirrored view instead: it puts north and east towards the camera, and
# both vanilla and this mod point a sunflower's face east, so at GUI_YAW the one block
# everybody recognises would be showing its back. Nothing else in the set reads worse.
#
# 240, not the 225 that would be the exact diagonal. A vanilla `cross` plant is two quads
# rotated 45 degrees about Y, so their normals sit on the diagonals at 45 and 315 -- and
# 225 is square-on to the first and *exactly* edge-on to the second. Every cross plant
# (grass, bushes, sugar cane, wildflowers) therefore rendered as a single flat billboard.
# Pitch cannot rescue it: the edge-on quad's normal is horizontal, so its dot product with
# the view direction stays zero however far you tilt. 15 degrees off the diagonal gives the
# second quad 26% of its width back -- enough to read as a plant -- while the pot itself
# still shows two faces at 0.87/0.50 and looks like the same three-quarter view. Past about
# 247.5 the pot starts reading as lopsided, which is the cost of going further.
BLOCK_YAW = 240.0

# Level.getShade(): a constant per face direction, applied instead of real lighting.
SHADE = {"down": 0.5, "up": 1.0, "north": 0.8, "south": 0.8, "west": 0.6, "east": 0.6}

NORMALS = {
    "down": (0.0, -1.0, 0.0),
    "up": (0.0, 1.0, 0.0),
    "north": (0.0, 0.0, -1.0),
    "south": (0.0, 0.0, 1.0),
    "west": (-1.0, 0.0, 0.0),
    "east": (1.0, 0.0, 0.0),
}

# FaceBakery scales a rotated element back out so it still spans its original bounds.
RESCALE = {22.5: 1.0 / math.cos(math.pi / 8.0), 45.0: 1.0 / math.cos(math.pi / 4.0)}
