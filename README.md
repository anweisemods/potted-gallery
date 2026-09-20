# potted-gallery

Renders every block a flower-pot mod adds — plus the item you pot each one with — straight
from the mod's own model JSON. No Minecraft client, no world, no GPU. A few seconds for a
hundred blocks, so it runs in CI on every push.

Built for [NotEnoughPots](https://github.com/anweisemods/NotEnoughPots) and
[PottedDelight](https://github.com/anweisemods/PottedDelight), but nothing in it is
specific to either: a mod describes itself in a `gallery.json` and the renderer reads its
blockstates.

## Output

| file | what it is |
| --- | --- |
| `overview.png` | a labelled contact sheet of everything |
| `blocks/<name>.png` | one transparent render per potted block |
| `items/<name>.png` | the source item's inventory icon |
| `index.html` | searchable gallery; click an entry for a rotatable 3D view |
| `models.js`, `textures/` | geometry and textures the 3D view uses |
| `banner.png` | the mod's wordmark, shown in the gallery header |
| `logo.png` | the mod's square icon, used in the header only when there is no wordmark |
| `favicon.png`, `apple-touch-icon.png` | 32px and 180px cuts of the square icon |
| `gallery.json` | manifest, if you want to build something else from it |

## Running it locally

```bash
pip install -r requirements.txt
PYTHONPATH=/path/to/potted-gallery python -m pottedgallery --config gallery.json
```

It needs the jars the mod builds against. Run the mod's Gradle build once first — the
renderer then finds the vanilla client jar in loom's cache on its own, and `gallery.json`
points at the rest.

```
--config PATH       the mod's gallery.json (default ./gallery.json)
--out DIR           default <repo>/build/gallery
--jar PATH          vanilla client jar, if it should not be auto-detected
--source PATH       extra resource dir or jar; repeatable, globs allowed
--size 256          pixels on the render's longer side
--overview-size 128 pixels per tile on the contact sheet
--ssaa 4            supersampling; 1 gives hard Minecraft-style edges
--columns 6         overview columns; beats gallery.json's "columns"
--theme dark|light
--biome plains      which biome colours the grass and foliage tints
--since-base        badge for entries above the first // <version>+ marker
--pitch / --yaw     camera angle, in this renderer's frame (see below)
--only REGEX        render a subset, for quick iteration
--strict            exit non-zero if any texture or item icon is unresolved
--no-overview / --no-html
```

## gallery.json

Lives in the mod's repository, next to `gradle.properties` — which supplies `mod_id`,
`mod_name`, `version` and `minecraft_version`, so the config only states what is left.
Every field is optional.

```json
{
  "name": "PottedDelight",
  "footer": "github.com/anweisemods/PottedDelight",
  "resources": ["common/src/main/resources"],
  "item_namespace": "farmersdelight",
  "item_aliases": { "tomatoes": "tomato_seeds" },
  "columns": 6,
  "sources": [
    "**/*FarmersDelight-${farmersdelight_fabric_full_version}.jar",
    "**/*notenoughpots-fabric-${notenoughpots_lib_version}.jar"
  ]
}
```

| key | meaning |
| --- | --- |
| `resources` | the mod's resource roots. Blockstates under `assets/<mod_id>/blockstates` are the catalogue. |
| `prefix` | stripped from a block name to get its label and source block. Default `potted_`. |
| `sources` | extra jars or directories, searched after the mod's own resources and the client jar. Globs, and `${...}` pulls a value from `gradle.properties`. |
| `banner` | the mod's wordmark. Defaults to `.github/assets/banner.png`, then `banner.png` at the repository root. When one exists the header shows it in place of the icon and the name, since a wordmark already spells the mod out. Transparent margins are cropped off, so the header can size every mod's wordmark the same way. |
| `author` | the name in the page footer's copyright line, beside the current year. Omit it and the line is left out. |
| `logo` | the mod's square icon. Defaults to `<mod_id>.png` at a resource root, which is where `fabric.mod.json` points its own `icon` — so it usually needs no setting. Always the favicon; also the header mark when there is no `banner`. |
| `item_namespace` | where to look for the item you pot a plant with. Default `minecraft`. |
| `item_aliases` | for plants whose item is named differently, or that have no item of their own: `{"carrots": "carrot"}`. |
| `columns` | cards per row on `overview.png`. Default 6. Wider sheets fit more mods on a screen; narrower ones keep the labels readable. |
| `tints` | tint layers the client registers in code, which no asset states. `"grass"`, `"foliage"`, `{"stem": 7}`, or `"#48b518"`. |
| `enum_java` | optional Java enum to read the source block and version badges from. |
| `since_base` | version badge for entries above the first `// <version>+` marker in that enum. |

**`${...}` references matter.** A build cache can hold several jars for the same
dependency, including a stale one for an older version, and picking the wrong one makes
models silently resolve to nothing. Pinning to the version the build already declares
avoids that; the renderer also skips empty jars and reports anything it could not resolve.

## In GitHub Actions

The only awkward input is the jars, and the answer is not to re-implement artifact
resolution: let the mod's own build download them, then render.

```yaml
- uses: actions/setup-java@v4
  with: { distribution: temurin, java-version: '21' }
- uses: gradle/actions/setup-gradle@v4
- run: ./gradlew :fabric:classes --no-daemon
- uses: anweisemods/potted-gallery@v1
  with:
    config: gallery.json
    out: build/gallery
- uses: actions/upload-artifact@v4
  with: { name: gallery, path: build/gallery }
```

`:fabric:classes` is enough — it pulls Minecraft and remaps the mod dependencies without
building jars. Loom leaves the client jar where the renderer looks for it.

Both mods carry a ready-made workflow at `.github/workflows/gallery.yml` that uploads the
gallery as an artifact per branch and publishes the default branch to GitHub Pages.

**Before turning Pages on**, note that `textures/` contains verbatim Minecraft and
dependency-mod texture files, which the 3D view loads. The rendered PNGs are renders, the
same as any screenshot on a mod page; republishing the source textures is a different
thing. Publish only `overview.png` and `blocks/` if you would rather not.

## How it works, and what bit while writing it

The renderer resolves the model JSON itself — parents, element rotations, `rescale`, face
UVs — and rasterises it with a small software renderer that copies Minecraft's block
rendering: the inventory camera angle, the fixed per-face diffuse shading (up 1.0, down
0.5, north/south 0.8, west/east 0.6) and the tint layers the client registers. The 3D view
in `index.html` ships the same quads, so it and the PNGs cannot disagree.

- **The camera yaw is not the number in the model file.** `minecraft:block/block` writes
  its GUI rotation as `[30, 225, 0]`, but that is measured through Minecraft's own GUI
  matrix stack, which mirrors an axis. In a plain right-handed frame 135 is the same view
  — checked against the furnace, the crafting table and the stairs, whose inventory icons
  pin down which two side faces you can see. Blocks are rendered at 225 anyway, the mirror
  of it, because both vanilla and these mods aim a sunflower's face east and the authentic
  angle would show the most recognisable block in the set from behind. Item icons keep
  135, because that is literally what an item frame shows.
- **A face's `uv` does not move the face.** It picks a different slice of the texture to
  stretch over the element's own rectangle. Deriving vertex positions from the stated UV
  instead of from `from`/`to` wrecks every model that reaches for a different part of
  `flower_pot.png`.
- **A UV rectangle is half open.** `[5, 5, 11, 11]` means texels 5..10. A sample landing
  on the far edge reads texel 11, which on `flower_pot.png` — 206 of whose 256 texels are
  transparent — is empty, so the alpha test drops the fragment and the backdrop shows
  through as a hairline along the edge of every face. Both renderers clamp the sample into
  the face's own texel range.
- **A multisampled WebGL canvas hands back premultiplied colour.** A half-covered edge
  pixel resolves to `(coverage x colour, coverage)`, so declaring `premultipliedAlpha:
  false` makes the compositor multiply by alpha a second time and every edge comes out
  darker and see-through. The 3D view uses an opaque buffer cleared to the page backdrop.
- **Backface culling is load bearing.** A cross model is two coplanar quads with opposite
  normals; without culling they z-fight instead of one of them simply facing you.
- **Translucency is decided per texture**, as the game has done since 26.1: a texture
  carrying partial alpha blends, everything else goes through a cutout alpha test.
- **Item models moved in 1.21.4.** Newer packs use `assets/<ns>/items/<id>.json`, which
  also carries the item's tints; older ones use `assets/<ns>/models/item/<id>.json` and
  tint in code only. Both are read.
