"""A very small HTML builder.

Building markup by concatenating f-strings is not wrong in itself -- these fragments are
a dozen elements, and a template engine would be a dependency and a second language for
no gain. What *is* wrong is that escaping then depends on the author remembering
`escape()` at every interpolation, and the one that gets forgotten is silent: it only
shows up when a mod is called `Tom & Jerry's Pots`.

So the only thing this module adds is that escaping is structural. `el()` escapes every
attribute value and every text child; markup that is already markup says so by being a
`Raw`, which is what nesting returns. There is no way to interpolate a string into a
document without it being escaped, which is the property a template engine would have
been bought for.
"""

from __future__ import annotations

from html import escape
from typing import Iterable


class Raw(str):
    """A string that is already markup and must not be escaped again."""

    __slots__ = ()


# HTML void elements: no closing tag, and a trailing slash would be noise.
VOID = frozenset({"br", "hr", "img", "input", "link", "meta", "source"})
# SVG is foreign content, where there are no void elements -- `<use href="#x">` stays
# open and swallows whatever follows it until the enclosing `</svg>`. These close
# themselves instead.
SELF_CLOSING = frozenset({"circle", "path", "rect", "use"})


def esc(value: object) -> Raw:
    """Text as markup. A `Raw` passes through; anything else is escaped."""
    return value if isinstance(value, Raw) else Raw(escape(str(value)))


def _attribute(name: str, value: object) -> str:
    # `class_` and `for_` dodge the Python keywords; an underscore anywhere else is a
    # hyphen, so `aria_hidden` writes `aria-hidden`.
    name = name.rstrip("_").replace("_", "-")
    if value is True:
        return f" {name}"  # a boolean attribute is its own presence
    return f' {name}="{escape(str(value), quote=True)}"'


def _open(tag: str, attributes: dict) -> str:
    return tag + "".join(_attribute(name, value) for name, value in attributes.items()
                         if value is not None and value is not False)


def el(tag: str, *children: object, **attributes: object) -> Raw:
    """One element. Children are escaped unless they are `Raw`; `None` and `False` drop out."""
    head = _open(tag, attributes)
    if tag in VOID:
        return Raw(f"<{head}>")
    if tag in SELF_CLOSING:
        return Raw(f"<{head}/>")
    return Raw(f"<{head}>{join(children)}</{tag}>")


def block(tag: str, *children: object, indent: str = "", **attributes: object) -> Raw:
    """`el`, but set out over several lines.

    `indent` is where the element itself sits in the template it is substituted into, so
    the fragment lands on the surrounding page's own indentation instead of arriving as
    one unreadable line. Nothing depends on it -- it is for whoever reads view-source.
    """
    inside = indent + "  "
    body = "".join(f"\n{inside}{esc(c)}" for c in children
                   if c is not None and c is not False and c != "")
    return Raw(f"<{_open(tag, attributes)}>{body}\n{indent}</{tag}>")


def join(children: Iterable[object], separator: str = "") -> Raw:
    """Concatenate children, escaping each one that is not already markup.

    `separator` is inserted verbatim: it is the caller's own punctuation or indentation,
    not content.
    """
    return Raw(separator.join(esc(c) for c in children
                              if c is not None and c is not False and c != ""))


def icon(name: str, size: int = 20, **attributes: object) -> Raw:
    """A reference into the page's `<symbol>` sprite, which is where every icon is defined."""
    return el("svg", el("use", href=f"#{name}"),
              **attributes, width=size, height=size, aria_hidden="true")
