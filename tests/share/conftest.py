import pytest


@pytest.fixture(autouse=True)
def _own_key_dir(tmp_path, monkeypatch):
    """The key that protects a stored password never lands in the developer's home folder."""
    monkeypatch.setenv("LOPANALYS_KEY_DIR", str(tmp_path / "keys"))
    from lopanalys.core import i18n
    i18n.set_language("sv")
