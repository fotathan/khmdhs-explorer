"""The production image copies app/ plus an explicit list of repo-root files.

A root module the web app imports but the Dockerfile does not copy works in
every test and locally, and fails only in production — tsg_match.py shipped
that way on 2026-09-17 and /act/TSG:… answered 500. Every root module imported
from app/ must be COPYed.
"""
from __future__ import annotations

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Imported only inside `except ImportError` fallbacks for running app/ modules
# as top-level scripts; the app/ package copy is what production uses.
FALLBACKS = {"crm", "interconnect", "search_profiles", "tables", "admin", "auth"}


def _root_modules() -> set[str]:
    return {p.stem for p in ROOT.glob("*.py")}


def _root_imports(path: pathlib.Path, roots: set[str]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        else:
            continue
        found.update(n for n in names if n in roots)
    return found


def _imported_by_app() -> set[str]:
    """Root modules app/ imports — and, transitively, what THEY import. The
    first miss was one step removed: tsg_match imports tsg_ingest (which imports
    tsg_probe) inside functions the production catch-up reaches."""
    roots = _root_modules()
    found: set[str] = set()
    for path in (ROOT / "app").rglob("*.py"):
        found |= _root_imports(path, roots)
    found -= FALLBACKS
    todo = list(found)
    while todo:
        mod = todo.pop()
        for dep in _root_imports(ROOT / f"{mod}.py", roots) - FALLBACKS - found:
            found.add(dep)
            todo.append(dep)
    return found


def _copied() -> set[str]:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    out: set[str] = set()
    for line in text.splitlines():
        if line.strip().startswith("COPY"):
            out.update(m.group(1) for m in re.finditer(r"(?<![\w/])([A-Za-z_]\w*)\.py\b", line))
    return out


def test_every_root_module_the_app_imports_is_in_the_image():
    missing = sorted(_imported_by_app() - _copied())
    assert not missing, f"app/ imports {missing} but the Dockerfile does not COPY them"


def test_the_check_sees_the_known_imports():
    assert {"tsg_match", "local_ocr", "khmdhs_ingest"} <= _imported_by_app()


def test_the_check_follows_imports_through_root_modules():
    assert {"tsg_ingest", "tsg_probe"} <= _imported_by_app()
