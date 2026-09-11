import ast
import sys
from pathlib import Path

import pytest

# Tests import `tests.pngwriter`; make the repo root importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CORPUS = Path.home() / "Pictures" / "wallpaper"


@pytest.fixture
def processing_dir(tmp_path):
    """A throwaway processing tree. Never the real one."""
    return tmp_path / "processing"


@pytest.fixture
def corpus():
    """The read-only image corpus, or skip. Never written to."""
    if not CORPUS.is_dir():
        pytest.skip(f"corpus not present at {CORPUS}")
    return CORPUS


def assert_pure_module(module, allowed):
    """Assert a module imports nothing outside `allowed`.

    Parses the source rather than matching strings: `from os import path`
    contains neither "import os" nor "from pathlib", so substring checks
    miss exactly the violations that matter. `allowed` is the set of
    top-level module names this module may import, relative imports
    included by their module name.
    """
    tree = ast.parse(Path(module.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # from . import sizes
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif node.module:                   # from os import path
                imported.add(node.module.split(".")[0])
    forbidden = imported - set(allowed)
    assert not forbidden, (
        f"{Path(module.__file__).name} imports {sorted(forbidden)}; "
        f"only {sorted(allowed)} allowed"
    )
