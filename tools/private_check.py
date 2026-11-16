"""Stop a commit that would publish private data: checks the staged version of each staged file with the
same rules as tests/tools/test_no_private_data.py (run by the pre-commit hook)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.tools.test_no_private_data import SELF, private_hits  # noqa: E402


def main() -> int:
    names = subprocess.run(["git", "-c", "core.quotepath=off", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                           cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    bad = []
    for name in names:
        if (ROOT / name).resolve() == SELF:
            continue
        blob = subprocess.run(["git", "show", f":{name}"], cwd=ROOT, capture_output=True, check=False).stdout
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue                      # images and other binary files
        bad += [f"{name}:{line}: {what}" for line, what, _w in private_hits(text)]
    for b in bad:
        print(f"private data: {b}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
