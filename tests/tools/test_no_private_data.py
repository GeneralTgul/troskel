"""The public repository holds no private data: no owner's name or home town, home machines, private repositories,
real device serial numbers or home network addresses. Made-up people and documentation addresses only."""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()

# The forbidden words are stored as hashes (the first 16 hex digits of SHA-256), so that this file does not
# publish them. EXACT is compared case-sensitively (a first name that is also an ordinary lower-case word);
# LOWER is compared on the lower-cased word. A word is a run of letters and digits.
EXACT = {
    "81f3bf42a93cf18d": "the owner's first name", "b8b5125225fa7722": "the owner's first name",
    "41b84db13b4ef5ad": "the owner's first name", "91376bfdbebb41d4": "the owner's first name",
}
LOWER = {
    "722050b2e05ba6b5": "the owner's surname", "d4bc5d3733d547b7": "the owner's home town",
    "97eda1329b653853": "the private repository", "9c120722ef33885b": "the private repository",
    "279e87868972611f": "a home machine", "1a62321497760833": "a home machine",
    "ceb4dd7555078b6e": "a home machine", "d20dbd6a870c2519": "a home machine",
    "4c304611222dbdfe": "a real serial number", "0cf36e0110ab196b": "a real serial number",
    "7eb88c065fe07416": "a real serial number",
}
PATTERNS = {
    "a home network address": r"\b192\.168\.\d{1,3}\.\d{1,3}\b",
    "a private e-mail domain": r"@(?:gmail|msn|hotmail|outlook)\.com\b",
}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".ttf", ".otf", ".ico", ".task", ".sqlite"}


def _h(word: str) -> str:
    return hashlib.sha256(word.encode("utf-8")).hexdigest()[:16]


def _files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
        names = [n for n in out.decode("utf-8").split("\0") if n]
    except (OSError, subprocess.CalledProcessError):
        names = [p.relative_to(ROOT).as_posix() for d in ("src", "tests", "docs", "tools", "scripts", ".github")
                 for p in (ROOT / d).rglob("*") if p.is_file()]
    return [ROOT / n for n in names if Path(n).suffix.lower() not in SKIP_SUFFIXES and (ROOT / n).resolve() != SELF]


def private_hits(text: str) -> list[tuple[int, str, str]]:
    """(line, what, match) for every forbidden word or pattern in ``text``."""
    hits = []
    for m in re.finditer(r"[A-Za-z0-9]+", text):
        w = m.group(0)
        what = EXACT.get(_h(w)) or LOWER.get(_h(w.lower()))
        if what:
            hits.append((text.count("\n", 0, m.start()) + 1, what, w))
    for what, rx in PATTERNS.items():
        for m in re.finditer(rx, text):
            hits.append((text.count("\n", 0, m.start()) + 1, what, m.group(0)))
    return hits


def test_the_check_finds_what_it_should():
    # Made-up stand-ins are not forbidden, the patterns are.
    assert private_hits("Olle Löpare, tom rad, 10.0.0.5, olle@example.se") == []
    assert [w for _, w, _ in private_hits("192.168.1.20 x@gmail.com")] == ["a home network address",
                                                                          "a private e-mail domain"]


def test_no_private_data_in_the_repository():
    found = []
    for path in _files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for line, what, match in private_hits(text):
            found.append(f"{path.relative_to(ROOT).as_posix()}:{line}: {what} ({match!r})")
    assert not found, "Private data in the repository:\n" + "\n".join(found[:50])
