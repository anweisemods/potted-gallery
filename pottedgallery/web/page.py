"""`index.html`: the page template, and the fragments the renderer fills it with.

The page is authored as three source files -- `page.html`, `page.css`, `page.js` --
because a single 1200-line HTML file had stopped being readable. They reference each
other with real `<link>` and `<script src>` tags, so the source tree stays a working
document you can open straight from disk, and `inline()` folds them together at build
time so the published gallery is still one self-contained file.

The handful of fragments that depend on the mod are built with `markup`, whose `el()`
escapes by construction. Anything longer-lived than a fragment belongs in `page.html`.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

from .markup import Raw, block, el, esc, icon, join

PAGE_TEMPLATE = Path(__file__).with_name("page.html")
PAGE_STYLE = Path(__file__).with_name("page.css")
PAGE_SCRIPT = Path(__file__).with_name("page.js")

STYLE_TAG = '<link rel="stylesheet" href="page.css">'
SCRIPT_TAG = '<script src="page.js"></script>'

ICON_LINKS = join([
    el("link", rel="icon", type="image/png", href="favicon.png"),
    el("link", rel="apple-touch-icon", href="apple-touch-icon.png"),
], separator="\n")

# Every `__NAME__` the template is allowed to contain. A placeholder in the template
# with nothing to fill it, or a value with no placeholder to go into, is a mistake, and
# a chain of `.replace()` calls cannot notice either: `__HEADING__` was still being
# substituted after the markup that used it had moved into `mark_markup`.
PLACEHOLDER = re.compile(r"__[A-Z]+__")

# Where each multi-line fragment sits in page.html, so a substituted block lands on the
# page's own indentation rather than as one very long line. Cosmetic, for view-source.
INDENT = {"__MARK__": "    ", "__DOWNLOADS__": "      ", "__FOOTER__": ""}


# --------------------------------------------------------------------------------------
# Fragments
# --------------------------------------------------------------------------------------

def footer_markup(author: str, footer: str, author_url: str = "") -> Raw:
    """The page footer: who made it, and where the source lives.

    `footer` is the same repository the overview sheet prints along its bottom edge,
    so the two cannot name different places. A repository link beats a profile link
    here because the page is about one mod, and that is the mod's repository.

    `author_url`, if set, makes the copyright name itself a link (to a profile, say),
    independent of the repository link beside it.
    """
    if not author and not footer:
        return Raw("")
    url = footer if footer.startswith(("http://", "https://")) else f"https://{footer}"
    year = datetime.date.today().year
    name = el("a", author, href=author_url) if author and author_url else author
    return block("footer",
                 block("div",
                       el("p", f"© {year} ", name) if author else None,
                       el("a", footer, href=url) if footer else None,
                       indent=INDENT["__FOOTER__"] + "  ", class_="wrap"),
                 indent=INDENT["__FOOTER__"], class_="footer")


# Stores the page knows how to label and badge. Anything else in `downloads` still
# renders -- title-cased, with the generic external-link mark -- so a mod can list a
# fourth place without this file having to learn about it first.
STORES = {
    "modrinth": ("Modrinth", "i-modrinth"),
    "curseforge": ("CurseForge", "i-curseforge"),
    "github": ("GitHub", "i-external"),
}


def downloads_markup(downloads: dict[str, str]) -> Raw:
    """The masthead's download button and the menu it opens.

    Returns "" when the mod declares no stores, and then page.js leaves the whole
    control alone -- there is no empty button and no dead menu.

    It borrows .picker/.picker-menu rather than introducing a second popup: the menu
    surface, the open animation and the clamp that keeps it on screen are all defined
    once, for the version picker, and a download list is the same object with rows
    that link out instead of rows that select.
    """
    if not downloads:
        return Raw("")
    outer = INDENT["__DOWNLOADS__"]
    rows = []
    for key, url in downloads.items():
        label, mark = STORES.get(key, (key.replace("-", " ").title(), "i-external"))
        rows.append(block("a",
                          icon(mark, class_="mark"), label,
                          icon("i-external", class_="go"),
                          indent=outer + "    ", class_="menu-link", role="menuitem",
                          href=url, target="_blank", rel="noopener noreferrer"))
    return block("div",
                 block("button",
                       icon("i-download", class_="lead"),
                       el("span", "Download", class_="label"),
                       icon("i-chevron-down", class_="chevron"),
                       indent=outer + "  ", class_="btn-text", id="downloads-button",
                       type="button", aria_haspopup="menu", aria_expanded="false"),
                 block("div", *rows,
                       indent=outer + "  ", class_="picker-menu", id="downloads-menu",
                       role="menu", aria_label="Download", hidden=True),
                 indent=outer, class_="picker", id="downloads")


def mark_markup(heading: str, logo: str | None, banner: str | None) -> Raw:
    """The header's brand block.

    With a wordmark the name is already drawn, so the <h1> goes visually hidden rather
    than being repeated in type beside it -- it still carries the document outline and
    is what a screen reader announces. Without one, the icon and a real heading.
    """
    newline = "\n" + INDENT["__MARK__"]
    if banner:
        return join([el("img", class_="wordmark", src=banner, alt=""),
                     el("h1", heading, class_="visually-hidden")], newline)
    if logo:
        return join([el("img", class_="logo", src=logo, alt=""),
                     el("h1", heading)], newline)
    return el("h1", heading)


def subheading_markup(facts: list[str]) -> Raw:
    """The masthead's caption, interleaved with the page's own divider.

    The dot only shows once the facts are on one line -- beside the wordmark they stack,
    and a dot between stacked rows would be a bullet. CSS decides which, so the markup
    carries both cases.
    """
    dot = el("span", Raw("&middot;"), class_="dot", aria_hidden="true")
    return join((el("span", fact) for fact in facts), separator=dot)


# --------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------

def inline(page: str) -> str:
    """Fold page.css and page.js into the markup."""
    style = PAGE_STYLE.read_text(encoding="utf-8")
    script = PAGE_SCRIPT.read_text(encoding="utf-8")
    if "</script" in script:
        raise SystemExit("page.js contains '</script', which would close the tag it is "
                         "inlined into")
    for tag in (STYLE_TAG, SCRIPT_TAG):
        if tag not in page:
            raise SystemExit(f"page.html no longer contains {tag!r}")
    return (page.replace(STYLE_TAG, f"<style>\n{style}</style>")
                .replace(SCRIPT_TAG, f"<script>\n{script}</script>"))


def _substitute(page: str, values: dict[str, str]) -> str:
    """Fill every `__NAME__` exactly once, and refuse if the two sides disagree.

    Inlining has already run, so `__ASPECT__` in the stylesheet is in scope here.
    """
    found = set(PLACEHOLDER.findall(page))
    unknown = found - values.keys()
    unused = values.keys() - found
    if unknown or unused:
        raise SystemExit(
            "page template and renderer disagree about placeholders"
            + (f"; template has no value for {sorted(unknown)}" if unknown else "")
            + (f"; renderer fills unused {sorted(unused)}" if unused else ""))
    # A function replacement, so nothing in a value is read back as a group reference.
    return PLACEHOLDER.sub(lambda m: values[m.group(0)], page)


def write_html(path: Path, *, title: str, heading: str, subheading: str, manifest: dict,
               aspect: float, logo: str | None, banner: str | None, icons: bool,
               footer: str, downloads: str) -> None:
    page = _substitute(inline(PAGE_TEMPLATE.read_text(encoding="utf-8")), {
        "__ASPECT__": f"{aspect:.4f}",
        "__TITLE__": esc(title),
        "__MARK__": mark_markup(heading, logo, banner),
        "__ICONS__": ICON_LINKS if icons else "",
        "__DOWNLOADS__": downloads,
        "__FOOTER__": footer,
        "__SUBHEADING__": subheading,
        # `</` inside a <script type="application/json"> would close it early.
        "__DATA__": json.dumps(manifest).replace("</", "<\\/"),
    })
    path.write_text(page, encoding="utf-8")
