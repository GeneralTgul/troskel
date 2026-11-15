"""The documentation tools in tools/: table of contents, index, checks and the screenshot list."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import docs_check  # noqa: E402
import docs_index  # noqa: E402
import docs_shots  # noqa: E402
import docs_toc  # noqa: E402


def put(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small repository that passes every check."""
    put(tmp_path / "README.md", "# Demo\n\nSe [dokumentationen](docs/index.md).\n")
    put(tmp_path / "docs/index.md", "# Dokumentation\n\n<!-- index -->\n<!-- /index -->\n")
    put(tmp_path / "docs/guide/guide.md", "# Guide\n\n*Ingress.*\n\n## Första steget\n\nText.\n\n### Detalj\n\nText.\n")
    put(tmp_path / "docs/guide/start/start.md", "# Starta\n\nTillbaka till [guiden](../guide.md#första-steget).\n")
    put(tmp_path / "tools/docs_index.yaml", "# comment\nHandbok:\n  guide/guide.md: En mening om guiden.\n")
    for p in docs_toc.stale(tmp_path, fix=True):
        assert p.name == "guide.md"
    docs_toc.write(tmp_path / "docs/index.md", docs_index.render(tmp_path))
    return tmp_path


def errors(root: Path) -> list[str]:
    return docs_check.check(root)[0]


def test_clean_repo_passes(repo: Path) -> None:
    assert errors(repo) == []


def test_toc_lists_headings_and_subpages(repo: Path) -> None:
    text = docs_toc.read(repo / "docs/guide/guide.md")
    assert text.startswith("# Guide\n\n*Ingress.*\n\n<!-- toc -->\n## Innehåll\n\n"
                           "- [Första steget](#första-steget)\n  - [Detalj](#detalj)\n\n"
                           "**Undersidor:**\n\n- [Starta](start/start.md)\n<!-- /toc -->\n\n## Första steget\n")
    assert docs_toc.stale(repo) == []          # a second run changes nothing


def test_toc_follows_a_new_heading_and_a_removed_subpage(repo: Path) -> None:
    page = repo / "docs/guide/guide.md"
    put(page, docs_toc.read(page) + "\n## Andra steget\n\n## Andra steget\n")
    (repo / "docs/guide/start/start.md").unlink()
    assert docs_toc.stale(repo) == [page]      # --check reports it without writing
    assert "toc: docs/guide/guide.md is out of date, run python tools/docs_toc.py" in errors(repo)
    docs_toc.stale(repo, fix=True)
    text = docs_toc.read(page)
    assert "- [Andra steget](#andra-steget)\n- [Andra steget](#andra-steget-1)\n<!-- /toc -->" in text
    assert "Undersidor" not in text


def test_anchor_matches_github() -> None:
    seen: dict[str, int] = {}
    assert docs_toc.anchor("Fyll i din klocka (Garmin)", seen) == "fyll-i-din-klocka-garmin"
    assert docs_toc.anchor("4. Säkerhet och ansvar", seen) == "4-säkerhet-och-ansvar"
    assert docs_toc.headings("```\n# not a heading\n```\n## **Fet** [länk](x.md)\n") == [(2, "Fet länk")]


def test_broken_link_is_found(repo: Path) -> None:
    put(repo / "docs/guide/start/start.md", "# Starta\n\nSe [sidan](../saknas.md).\n")
    assert errors(repo) == ["link: docs/guide/start/start.md: ../saknas.md does not exist"]


def test_broken_anchor_is_found(repo: Path) -> None:
    put(repo / "docs/guide/start/start.md", "# Starta\n\nSe [steget](../guide.md#tredje-steget).\n")
    assert errors(repo) == ["link: docs/guide/start/start.md: the anchor #tredje-steget does not exist in docs/guide/guide.md"]


def test_links_in_code_and_to_the_web_are_left_alone(repo: Path) -> None:
    put(repo / "docs/guide/start/start.md",
        "# Starta\n\n`[x](saknas.md)` och [webben](https://example.org/saknas) och\n\n```\n[y](saknas.md)\n```\n")
    assert errors(repo) == []


def test_page_without_index_row_is_an_error(repo: Path) -> None:
    put(repo / "docs/ny/ny.md", "# Ny sida\n")
    assert errors(repo) == ["index: ny/ny.md: has no row in tools/docs_index.yaml (group, path and one sentence)"]
    put(repo / "tools/docs_index.yaml", "Handbok:\n  guide/guide.md: En mening om guiden.\n  ny/ny.md: Den nya sidan.\n")
    assert errors(repo) == ["index: docs/index.md is out of date, run python tools/docs_index.py"]
    docs_toc.write(repo / "docs/index.md", docs_index.render(repo))
    assert errors(repo) == []


def test_index_is_sorted_with_subpages_indented(repo: Path) -> None:
    put(repo / "docs/alfa/alfa.md", "# Öppna – sist i svensk ordning\n")
    put(repo / "tools/docs_index.yaml",
        "Handbok:\n  alfa/alfa.md: \"Citerad: mening.\"\n  guide/guide.md: En mening om guiden.\n")
    assert docs_index.block(repo, docs_index.load_groups(repo / "tools/docs_index.yaml")) == (
        "## Handbok\n\n- [Guide](guide/guide.md): En mening om guiden.\n  - [Starta](guide/start/start.md)\n"
        "- [Öppna](alfa/alfa.md): Citerad: mening.\n")


def test_index_row_without_page_and_long_sentence(repo: Path) -> None:
    put(repo / "tools/docs_index.yaml", f"Handbok:\n  guide/guide.md: {'x' * 101}\n  borta/borta.md: Finns inte.\n")
    assert docs_index.problems(repo) == ["guide/guide.md: the sentence is 101 characters (at most 100)",
                                        "borta/borta.md: has a row in tools/docs_index.yaml but does not exist"]


def test_missing_misplaced_and_unused_images(repo: Path) -> None:
    put(repo / "docs/guide/images/kvar.png", "x")
    put(repo / "docs/guide/start/images/bild.png", "x")
    put(repo / "docs/guide/guide.md", docs_toc.read(repo / "docs/guide/guide.md")
        + "\n![saknas](images/saknas.png)\n![fel mapp](start/images/bild.png)\n")
    assert errors(repo) == ["image: docs/guide/guide.md: images/saknas.png is missing",
                            "image: docs/guide/guide.md: start/images/bild.png lies outside the page's own images/ folder",
                            "image: docs/guide/images/kvar.png is not shown by any page"]


def test_page_outside_its_own_folder_is_an_error(repo: Path) -> None:
    put(repo / "docs/guide/Extra.md", "# Extra\n")
    put(repo / "docs/guide/Stor_Mapp/Stor_Mapp.md", "# Stor\n")
    found = [e for e in errors(repo) if e.startswith("layout")]
    assert found == ["layout: docs/guide/Extra.md: a page lies in its own folder, <name>/<name>.md",
                     "layout: docs/guide/Stor_Mapp/Stor_Mapp.md: folder and file names are lower-case kebab-case"]


def test_retired_word_warns_unless_the_line_says_it_is_old(repo: Path) -> None:
    put(repo / "tools/docs_retired.yaml", "Pensionerade ord:\n  \"Starta livevyn\": knappen heter Starta testet\n")
    put(repo / "docs/guide/start/start.md", "# Starta\n\nTryck på Starta livevyn.\n\nKnappen hette Starta livevyn.\n")
    errs, warnings = docs_check.check(repo)
    assert errs == []
    assert warnings == ["retired: docs/guide/start/start.md:3: 'Starta livevyn' is retired; now: knappen heter Starta testet"]


def test_shot_list_must_point_at_real_pages_and_known_views(repo: Path) -> None:
    shots = [{"view": "idrottare", "page": "docs/guide/guide.md", "file": "idrottare.png"},
             {"view": "okänd", "page": "docs/guide/guide.md", "file": "x.png"},
             {"view": "testerna", "page": "docs/borta/borta.md", "file": "testerna.png"}]
    put(repo / "tools/docs_shots.json", json.dumps({"shots": shots}))
    assert docs_shots.target(repo, shots[0]) == repo / "docs/guide/images/idrottare.png"
    assert [e for e in errors(repo) if e.startswith("shots")] == [
        "shots: tools/docs_shots.json: unknown view 'okänd'",
        "shots: tools/docs_shots.json: the page docs/borta/borta.md for 'testerna' does not exist"]
    assert docs_shots.missing(repo) == ["docs/guide/images/idrottare.png", "docs/guide/images/x.png"]


def test_every_view_in_the_shot_list_has_a_method() -> None:
    for view in docs_shots.VIEWS:
        assert callable(getattr(docs_shots.Studio, view.replace("-", "_"))), view


def test_screenshot_is_only_rewritten_when_it_changed(tmp_path: Path) -> None:
    from PIL import Image
    base = Image.new("RGB", (200, 100), (20, 26, 33))
    base.save(tmp_path / "old.png")
    noisy = base.copy()
    noisy.putpixel((5, 5), (255, 255, 255))                 # one pixel of 20 000: noise
    noisy.paste((24, 30, 37), (0, 0, 200, 50))              # a faint shade: noise
    noisy.save(tmp_path / "noisy.png")
    changed = base.copy()
    changed.paste((255, 255, 255), (10, 10, 60, 30))        # a new label
    changed.save(tmp_path / "changed.png")
    old_bytes = (tmp_path / "old.png").read_bytes()
    assert docs_shots.save_if_changed(tmp_path / "noisy.png", tmp_path / "old.png") is False
    assert (tmp_path / "old.png").read_bytes() == old_bytes
    assert docs_shots.save_if_changed(tmp_path / "changed.png", tmp_path / "old.png") is True
    assert not (tmp_path / "changed.png").exists()
    base.resize((100, 50)).save(tmp_path / "small.png")
    assert docs_shots.differs(tmp_path / "old.png", tmp_path / "small.png")
    assert docs_shots.save_if_changed(tmp_path / "small.png", tmp_path / "images" / "new.png") is True


def test_the_repository_documentation_passes() -> None:
    errs, _ = docs_check.check(REPO)
    assert errs == []
    assert docs_shots.missing(REPO) == []
