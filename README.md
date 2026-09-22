# potted-gallery

Renders every block a flower-pot mod adds, plus the item you pot each one with, straight from the
mod's own model JSON. No Minecraft client, no world, no GPU.

Live: **[NotEnoughPots](https://nep.mods.anweisen.net)** · **[PottedDelight](https://pd.mods.anweisen.net)**

[![PottedDelight's contact sheet](https://pd.mods.anweisen.net/overview.png)](https://pd.mods.anweisen.net)

A mod describes itself in one `gallery.json`. The renderer reads its blockstates and works out the
rest, so adding a block adds it to the gallery with no further config.

## Use it in a workflow

The jars are the awkward part, and the fix is to let the mod's own build fetch them first.

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

`:fabric:classes` pulls Minecraft and remaps the mod dependencies without building jars, and Loom
leaves the client jar where the renderer looks for it.

Every input is optional: `config`, `out`, `jar`, `sources`, `args`, `strict` (default `true`),
`python-version`. Outputs are `directory` and `overview`.

Both mods carry a ready-made workflow at `.github/workflows/gallery.yml` that uploads the gallery
per branch and publishes the default branch to GitHub Pages.

## What it writes

| file | |
| --- | --- |
| `index.html` | searchable gallery, self-contained; click an entry for a rotatable 3D view |
| `overview.png` | a labelled contact sheet of everything |
| `blocks/<name>.png` | one transparent render per potted block |
| `items/<name>.png` | the source item's inventory icon |
| `gallery.json` | manifest, if you want to build something else from it |

Plus `models.js` and `textures/` for the 3D view, and `banner.png` / `logo.png` / `favicon.png` /
`apple-touch-icon.png` cut from the mod's own artwork.

**Before publishing it anywhere**, note that `textures/` holds verbatim Minecraft and
dependency-mod texture files, because the 3D view loads them. The rendered PNGs are renders, the
same as any screenshot on a mod page; republishing the source textures is a different thing.
Publish only `overview.png` and `blocks/` if you would rather not.

## gallery.json

Lives next to `gradle.properties`, which already supplies `mod_id`, `mod_name`, `version` and
`minecraft_version`. Every key below is optional.

```json
{
  "name": "PottedDelight",
  "footer": "github.com/anweisemods/PottedDelight",
  "author": "anweisen",
  "downloads": {
    "modrinth": "https://modrinth.com/mod/potted-delight"
  },
  "resources": ["common/src/main/resources"],
  "item_namespace": "farmersdelight",
  "item_aliases": { "tomatoes": "tomato_seeds" },
  "sources": [
    "**/*FarmersDelight-${farmersdelight_fabric_full_version}.jar"
  ]
}
```

| key | meaning |
| --- | --- |
| `resources` | the mod's resource roots. Blockstates under `assets/<mod_id>/blockstates` are the catalogue. |
| `sources` | extra jars or directories, searched after the mod's own resources and the client jar. Globs, and `${...}` pulls a value from `gradle.properties`. |
| `prefix` | stripped from a block name to get its label and source block. Default `potted_`. |
| `item_namespace` | where to look for the item you pot a plant with. Default `minecraft`. |
| `item_aliases` | for plants whose item is named differently, or that have none of their own: `{"carrots": "carrot"}`. |
| `tints` | tint layers the client registers in code, which no asset states: `"grass"`, `"foliage"`, `{"stem": 7}`, or `"#48b518"`. |
| `downloads` | store links for the page's Download menu, in the order you list them. Modrinth, CurseForge and GitHub get their own mark. |
| `author`, `footer` | the page footer's copyright line and repository link. |
| `banner` | the mod's wordmark, shown in the header instead of the icon and the name. Defaults to `.github/assets/banner.png`, then `banner.png` at the repository root. Transparent margins are cropped, so every mod's wordmark ends up the same size. |
| `logo` | the mod's square icon. Defaults to `<mod_id>.png` at a resource root, which is where `fabric.mod.json` already points, so it usually needs no setting. |
| `columns` | cards per row on `overview.png`. Default 8. |
| `enum_java` | a Java enum to read the source block and version badges from. Without it the gallery is alphabetical and carries no badges. |
| `since_base` | version badge for entries above the first `// <version>+` marker in that enum. |

**Pin your `${...}` references.** A build cache can hold several jars for the same dependency,
including a stale empty one for an older version, and picking the wrong one makes models resolve to
nothing without saying so. The renderer skips empty jars and reports anything it could not resolve;
`--strict` turns that into a failed job.

## Run it locally

```bash
pip install -r requirements.txt
PYTHONPATH=/path/to/potted-gallery python -m pottedgallery --config gallery.json
```

Needs Python 3.9+, Pillow and numpy, and the jars the mod builds against. Run the mod's Gradle
build once first; the renderer then finds the vanilla client jar in Loom's cache on its own.

```
--config PATH       the mod's gallery.json (default ./gallery.json)
--out DIR           default <repo>/build/gallery
--jar PATH          vanilla client jar, if it should not be auto-detected
--source PATH       extra resource dir or jar; repeatable, globs allowed
--size 256          pixels on the render's longer side
--overview-size 128 pixels per tile on the contact sheet
--ssaa 4            supersampling; 1 gives hard Minecraft-style edges
--columns 8         overview columns; beats gallery.json's "columns"
--theme dark|light  the contact sheet's theme
--biome plains      which biome colours the grass and foliage tints
--pitch / --yaw     camera angle
--only REGEX        render a subset, for quick iteration
--strict            exit non-zero if any texture or item icon is unresolved
--no-overview / --no-html
```

## How it works

It resolves the model JSON itself, including parents, element rotations, `rescale` and face UVs,
then rasterises it with a small software renderer that copies Minecraft's block rendering: the
inventory camera angle, the fixed per-face shading (up 1.0, down 0.5, north/south 0.8, west/east
0.6) and the tint layers the client registers at runtime. The 3D view ships the same quads, so it
and the PNGs cannot disagree about a model.

`pottedgallery/mc/` is that renderer and knows nothing about this tool. `pottedgallery/web/` builds
the page from `page.html`, `page.css` and `page.js`, which are real files inlined into one
self-contained `index.html` at build time.

## Licence

None yet, so default copyright applies.
