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
