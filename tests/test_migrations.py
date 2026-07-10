"""Migration bookkeeping integrity (mirrors migrate.py's own path resolution)."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _manifest_entries():
    lines = (ROOT / "migrations" / "manifest.txt").read_text().splitlines()
    return [s for s in (ln.strip() for ln in lines) if s and not s.startswith("#")]


def test_manifest_nonempty():
    assert len(_manifest_entries()) >= 1


def test_every_manifest_file_exists_and_nonempty():
    # migrate.py resolves each entry as ROOT/<entry> (bare = repo root,
    # 'migrations/x.sql' = under migrations/). A dangling entry breaks `up`.
    missing, empty = [], []
    for rel in _manifest_entries():
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
        elif p.stat().st_size == 0:
            empty.append(rel)
    assert not missing, f"manifest references missing files: {missing}"
    assert not empty, f"empty migration files: {empty}"


def test_no_duplicate_manifest_entries():
    entries = _manifest_entries()
    assert len(entries) == len(set(entries)), "duplicate manifest entries"


def test_migrate_tool_importable():
    import migrate
    assert hasattr(migrate, "cmd_up") and hasattr(migrate, "cmd_status")
