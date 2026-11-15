"""Writes the table of contents between ``<!-- toc -->`` and ``<!-- /toc -->`` in the pages under docs/.

The block lists the page's ``##`` and ``###`` headings with GitHub-compatible anchors, and after
them the page's sub-pages (``<page>/<sub>/<sub>.md``) under "Undersidor", each with its H1 as the
link text. A page with the two markers gets the block rewritten in place. A page with sub-pages but
without markers gets the block inserted after the H1 (and an italic intro line). Running it twice
changes nothing.

    python tools/docs_toc.py [page.md ...]   write (all pages when none are given); prints changed files
    python tools/docs_toc.py --check         only report; exit 1 when a block is out of date
"""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- toc -->", "<!-- /toc -->"
TITLE = "## Innehåll"
LEVELS = (2, 3)
SV = str.maketrans({"å": "{", "ä": "|", "ö": "}"})  # Swedish sort order: å, ä, ö after z


def sort_key(text: str) -> str:
    return text.lower().translate(SV)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def anchor(text: str, seen: dict[str, int]) -> str:
    """The anchor GitHub gives a heading: lower case, punctuation dropped, spaces to hyphens."""
    a = unicodedata.normalize("NFC", text).lower()
    a = re.sub(r"[^\w\- ]", "", a).strip().replace(" ", "-")
    n = seen.get(a, 0)
    seen[a] = n + 1
    return a if n == 0 else f"{a}-{n}"


def headings(text: str) -> list[tuple[int, str]]:
    """(level, plain text) for every heading outside code fences."""
    out, fence = [], False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            fence = not fence
            continue
        m = None if fence else re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if not m:
            continue
        plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", m.group(2))   # [text](link) -> text
        plain = re.sub(r"\*\*(.+?)\*\*", r"\1", plain).strip("*").strip()
        out.append((len(m.group(1)), plain))
    return out


def anchors(text: str) -> set[str]:
    """Every anchor a link may point at in this page."""
    seen: dict[str, int] = {}
    out = {anchor(h, seen) for _, h in headings(text)}
    return out | set(re.findall(r'<a\s+(?:name|id)="([^"]+)"', text))


def title_of(path: Path) -> str:
    """The page's H1, or the file name when it has none."""
    for level, text in headings(read(path)):
        if level == 1:
            return text
    return path.stem


def subpages(path: Path) -> list[Path]:
    """``<dir>/<sub>/<sub>.md`` for the page ``<dir>/<dir>.md``, sorted by title."""
    if path.stem != path.parent.name:
        return []
    subs = [d / f"{d.name}.md" for d in path.parent.iterdir() if d.is_dir()]
    return sorted((p for p in subs if p.is_file()), key=lambda p: sort_key(title_of(p)))


def build_block(text: str, subs: list[tuple[str, str]]) -> list[str]:
    seen: dict[str, int] = {}
    items = []
    for level, h in headings(text):
        if level == 1:
            continue
        a = anchor(h, seen)   # count every heading, so duplicates get the same suffix as on GitHub
        if level in LEVELS and h != TITLE[3:]:
            items.append("  " * (level - 2) + f"- [{h}](#{a})")
    if subs:
        items += ([""] if items else []) + ["**Undersidor:**", ""] + [f"- [{t}]({link})" for t, link in subs]
    return [START, TITLE, ""] + items + [END]


def render(path: Path) -> str | None:
    """The page with a current block, or None when the page has neither markers nor sub-pages."""
    text = read(path)
    lines = text.split("\n")
    subs = [(title_of(p), f"{p.parent.name}/{p.name}") for p in subpages(path)]
    block = build_block(text, subs)
    if START in lines and END in lines:
        i, j = lines.index(START), lines.index(END)
        return "\n".join(lines[:i] + block + lines[j + 1:])
    if not subs or not lines[0].startswith("# "):
        return None
    k = 1
    while k < len(lines) and not lines[k].strip():
        k += 1
    if k < len(lines) and lines[k].startswith("*") and not lines[k].startswith("**"):
        k += 1   # the italic intro belongs to the H1
    rest = lines[k:]
    while rest and not rest[0].strip():
        rest = rest[1:]
    return "\n".join(lines[:k] + [""] + block + [""] + rest)


def pages(root: Path) -> list[Path]:
    return sorted((root / "docs").rglob("*.md"))


def stale(root: Path, only: list[Path] | None = None, fix: bool = False) -> list[Path]:
    """Pages whose block is out of date; with ``fix`` they are rewritten."""
    out = []
    for p in only if only else pages(root):
        new = render(p)
        if new is not None and new != read(p):
            out.append(p)
            if fix:
                write(p, new)
    return out


def main(argv: list[str]) -> int:
    check = "--check" in argv
    only = [Path(a).resolve() for a in argv if not a.startswith("--")]
    only = [p for p in only if p.suffix == ".md" and p.is_file() and (ROOT / "docs") in p.parents]
    if any(not a.startswith("--") for a in argv) and not only:
        return 0   # files were given, but none of them is a page under docs/
    changed = stale(ROOT, only, fix=not check)
    for p in changed:
        print(p.relative_to(ROOT).as_posix())
    return 1 if check and changed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
