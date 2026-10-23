from pathlib import Path


def test_sources_do_not_name_the_other_project():
    root = Path(__file__).resolve().parents[1] / "src"
    for path in root.rglob("*.py"):
        lowered = path.read_text(encoding="utf-8").lower()
        assert "peach" not in lowered, path


def test_readme_mentions_the_inspiration_once():
    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert text.lower().count("peach") == 1
