"""Writes the index in docs/index.md from tools/docs_index.yaml.

The yaml file has one heading per group and one row per page: the path under docs/ and one sentence
about the page. The title is the page's H1 up to the first dash or colon, the pages are sorted by title
within the group, and sub-pages (``<page>/<sub>/<sub>.md``) are listed indented under their page.
The lists are written between ``<!-- index -->`` and ``<!-- /index -->``.

    python tools/docs_index.py           write
    python tools/docs_index.py --check   exit 1 when the index is out of date, a page has no row,
                                         a row has no page or a sentence is too long
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from docs_toc import read, sort_key, subpages, title_of, write  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- index -->", "<!-- /index -->"
MAX_LEN = 100
UNLISTED = {"index.md"}   # the index does not list itself


def load_groups(path: Path) -> dict[str, dict[str, str]]:
    """Read the two-level mapping ``group: / key: value`` (the small part of YAML the docs tools use)."""
    out: dict[str, dict[str, str]] = {}
    group: dict[str, str] | None = None
    for n, raw in enumerate(read(path).split("\n"), 1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" ") and line.endswith(":"):
            group = out.setdefault(line[:-1].strip().strip("\"'"), {})
            continue
        m = re.match(r"^\s+(\"[^\"]+\"|'[^']+'|[^:]+):\s+(.*)$", line)
        if m is None or group is None:
            raise ValueError(f"{path.name}:{n}: expected 'Group:' or an indented 'key: value' row")
        value = m.group(2).strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        group[m.group(1).strip().strip("\"'")] = value
    return out


def title(path: Path) -> str:
    """The link text: the H1 up to the first dash or colon."""
    return re.split(r" [—–] |: ", title_of(path))[0].strip()


def block(root: Path, groups: dict[str, dict[str, str]]) -> str:
    docs = root / "docs"
    lines: list[str] = []

    def tree(page: Path, depth: int) -> None:
        for sub in subpages(page):
            lines.append(f"{'  ' * depth}- [{title(sub)}]({sub.relative_to(docs).as_posix()})")
            tree(sub, depth + 1)

    for group, rows in groups.items():
        lines += [f"## {group}", ""]
        present = [r for r in rows if (docs / r).is_file()]
        for rel in sorted(present, key=lambda r: sort_key(title(docs / r))):
            lines.append(f"- [{title(docs / rel)}]({rel}): {rows[rel]}")
            tree(docs / rel, 1)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def problems(root: Path) -> list[str]:
    """Pages without a row, rows without a page and sentences that are too long."""
    docs = root / "docs"
    rows = {rel: text for g in load_groups(root / "tools" / "docs_index.yaml").values() for rel, text in g.items()}
    out = [f"{rel}: the sentence is {len(t)} characters (at most {MAX_LEN})" for rel, t in rows.items() if len(t) > MAX_LEN]
    out += [f"{rel}: has a row in tools/docs_index.yaml but does not exist" for rel in rows if not (docs / rel).is_file()]
    covered = set()
    stack = [docs / rel for rel in rows if (docs / rel).is_file()]
    while stack:
        p = stack.pop()
        covered.add(p)
        stack += subpages(p)
    for p in sorted(docs.rglob("*.md")):
        rel = p.relative_to(docs).as_posix()
        if p not in covered and rel not in UNLISTED:
            out.append(f"{rel}: has no row in tools/docs_index.yaml (group, path and one sentence)")
    return out


def render(root: Path) -> str:
    """docs/index.md with the current lists between the markers."""
    text = read(root / "docs" / "index.md")
    i, j = text.index(START), text.index(END)
    return text[:i] + START + "\n" + block(root, load_groups(root / "tools" / "docs_index.yaml")) + text[j:]


def main(argv: list[str]) -> int:
    check = "--check" in argv
    probs = problems(ROOT)
    for p in probs:
        print("index:", p)
    index = ROOT / "docs" / "index.md"
    new = render(ROOT)
    if new != read(index):
        print(("out of date: " if check else "") + "docs/index.md")
        if not check:
            write(index, new)
    return 1 if probs or (check and new != read(index)) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
