"""Checks the documentation: links, anchors, images, where pages lie, the index and the screenshot list.

Runs from the pre-commit hook and by hand. The rules are described in docs/dokumentationsrutin.md.

    python tools/docs_check.py    check README.md, README.sv.md, CONTRIBUTING.md and every page under docs/; exit 1 on errors

Errors (stop the commit):
  link     a relative link to a file that does not exist, or to an anchor (#heading) that does not exist
  image    an image the page shows that is missing or lies outside the page's own images/ folder,
           and an image in an images/ folder that no page shows. The README pages in the root show
           their own images from docs/images/readme/, and may show the handbook's images
  layout   a page that is not docs/.../<name>/<name>.md in lower-case kebab-case
           (docs/index.md, docs/dokumentationsrutin.md and the older pages in LEGACY are exempt)
  index    a page without a row in tools/docs_index.yaml, a row without a page,
           or a docs/index.md that is not what tools/docs_index.py writes
  toc      a table of contents that is not what tools/docs_toc.py writes
  shots    a row in tools/docs_shots.json that points at a page that does not exist
Warnings (printed, do not stop):
  retired  a retired word from tools/docs_retired.yaml on a line that does not say it is the old name
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docs_index  # noqa: E402
import docs_toc  # noqa: E402
from docs_toc import read  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOOSE = {"index.md", "dokumentationsrutin.md"}
# Pages from before the folder rule. Issues and other repositories link to these addresses,
# so they stay where they are. A new page never goes into this list.
LEGACY = {"ARKITEKTUR.md", "DATASKYDD.md", "PROV.md", "TRANINGSPLAN.md",
          "sporter/bikefit-och-stakteknik.md", "sporter/enheter.md", "sporter/ombyggnad.md",
          "sporter/tester-och-zoner.md"}
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|<img[^>]+src=\"([^\"]+)\"")
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
SLUG = re.compile(r"^[a-z0-9åäö]+(-[a-z0-9åäö]+)*$")
OLD_NAME = re.compile(r"tidigare|hette|förut|ersatt", re.I)


def strip_code(text: str) -> str:
    """The text without code blocks and inline code (they hold examples, not links)."""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", text)


def external(target: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9+.-]*:", target)) or target.startswith("//")


def check_links(root: Path, page: Path, text: str, errors: list[str]) -> None:
    for m in LINK.finditer(strip_code(text)):
        target = m.group(1).strip("<>")
        if external(target):
            continue
        rel, _, frag = target.partition("#")
        dest = (page.parent / unquote(rel)).resolve() if rel else page
        if not dest.exists():
            errors.append(f"link: {name(root, page)}: {target} does not exist")
        elif frag and dest.suffix == ".md" and unquote(frag) not in docs_toc.anchors(read(dest)):
            errors.append(f"link: {name(root, page)}: the anchor #{unquote(frag)} does not exist in {name(root, dest)}")


def allowed_image(root: Path, page: Path, dest: Path) -> bool:
    """A page under docs/ shows only its own images/; a README in the root shows docs/images/readme/
    and the images of the handbook pages."""
    if page.parent.resolve() == root and page.name in README_PAGES:
        return dest.parent == (root / "docs/images/readme").resolve() or (
            dest.parent.name == "images" and (root / "docs/handbok").resolve() in dest.parents)
    return dest.parent == (page.parent / "images").resolve()


def check_images(root: Path, page: Path, text: str, errors: list[str], used: set[Path]) -> None:
    for m in IMAGE.finditer(strip_code(text)):
        src = unquote(m.group(1) or m.group(2))
        if external(src):
            continue
        dest = (page.parent / src).resolve()
        used.add(dest)
        if not dest.is_file():
            errors.append(f"image: {name(root, page)}: {src} is missing")
        elif not allowed_image(root, page, dest):
            errors.append(f"image: {name(root, page)}: {src} lies outside the page's own images/ folder")


def check_layout(root: Path, page: Path, errors: list[str]) -> None:
    rel = page.relative_to(root / "docs").as_posix()
    if rel in LOOSE or rel in LEGACY:
        return
    if page.stem != page.parent.name:
        errors.append(f"layout: docs/{rel}: a page lies in its own folder, <name>/<name>.md")
    elif not all(SLUG.match(part) for part in page.relative_to(root / "docs").parent.parts):
        errors.append(f"layout: docs/{rel}: folder and file names are lower-case kebab-case")


def check_unused_images(root: Path, used: set[Path], errors: list[str]) -> None:
    for d in [*(root / "docs").rglob("images"), root / "docs/images/readme"]:
        for f in sorted(d.iterdir()) if d.is_dir() else []:
            if f.suffix.lower() in IMAGE_TYPES and f.resolve() not in used:
                errors.append(f"image: {f.relative_to(root).as_posix()} is not shown by any page")


def check_shots(root: Path, errors: list[str]) -> None:
    cfg = root / "tools" / "docs_shots.json"
    if cfg.is_file():
        import docs_shots
        errors += [f"shots: {p}" for p in docs_shots.problems(root)]


def retired_words(root: Path, pages: list[Path]) -> list[str]:
    cfg = root / "tools" / "docs_retired.yaml"
    words = {w: now for g in docs_index.load_groups(cfg).values() for w, now in g.items()} if cfg.is_file() else {}
    out = []
    for page in pages:
        if page.name == "dokumentationsrutin.md":
            continue   # the routine may name old words as examples
        for n, line in enumerate(read(page).split("\n"), 1):
            for word, now in words.items():
                if word in line and not OLD_NAME.search(line):
                    out.append(f"retired: {page.relative_to(root).as_posix()}:{n}: '{word}' is retired; now: {now}")
    return out


ROOT_PAGES = ("README.md", "README.sv.md", "CONTRIBUTING.md")   # English entry, Swedish entry, the DCO
README_PAGES = ("README.md", "README.sv.md")


def name(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix() if root in p.parents else str(p)


def check(root: Path) -> tuple[list[str], list[str]]:
    """(errors, warnings) for the pages in the root (``ROOT_PAGES``) and every page under docs/."""
    root = root.resolve()
    docs = sorted((root / "docs").rglob("*.md"))
    pages = [root / n for n in ROOT_PAGES if (root / n).is_file()] + docs
    errors: list[str] = []
    used: set[Path] = set()
    for page in pages:
        text = read(page)
        check_links(root, page, text, errors)
        if page in docs or page.name in README_PAGES:
            check_images(root, page, text, errors, used)
        if page in docs:
            check_layout(root, page, errors)
    check_unused_images(root, used, errors)
    errors += [f"index: {p}" for p in docs_index.problems(root)]
    if (root / "docs" / "index.md").is_file() and docs_index.render(root) != read(root / "docs" / "index.md"):
        errors.append("index: docs/index.md is out of date, run python tools/docs_index.py")
    errors += [f"toc: {name(root, p)} is out of date, run python tools/docs_toc.py" for p in docs_toc.stale(root)]
    check_shots(root, errors)
    return errors, retired_words(root, pages)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    errors, warnings = check(ROOT)
    for w in warnings:
        print("warning:", w)
    for e in errors:
        print("ERROR:", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
