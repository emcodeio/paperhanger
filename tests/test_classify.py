# tests/test_classify.py
import pytest

from paperhanger import sizes
from paperhanger.classify import CROP, classify

D, P = sizes.DESKTOP, sizes.PHONE


@pytest.mark.parametrize("width,height,device,expected", [
    # --- desktop ---
    (1600, 1000, D, sizes.DESKTOP_BY_WIDTH),    # exactly 1.60
    (1599, 1000, D, sizes.DESKTOP_BY_WIDTH),
    (1605, 1000, D, sizes.DESKTOP_BY_HEIGHT),   # legacy said by_width; see 4.1
    (1920, 1080, D, sizes.DESKTOP_BY_HEIGHT),   # 16:9
    (1001, 1000, D, sizes.DESKTOP_BY_WIDTH),    # just wider than square
    (1000, 1000, D, CROP),                      # square crops
    (1000, 1001, D, CROP),
    (2048, 4096, D, CROP),
    # --- phone ---
    (2000, 3000, P, sizes.PHONE_BY_HEIGHT),     # exactly 2:3; legacy said by_width
    (1999, 3000, P, sizes.PHONE_BY_WIDTH),      # a hair thinner than 2:3
    (1206, 2622, P, sizes.PHONE_BY_WIDTH),      # an iPhone screenshot
    (1000, 1001, P, sizes.PHONE_BY_HEIGHT),
    (1000, 1000, P, CROP),                      # square crops
    (4000, 3000, P, CROP),
])
def test_classification(width, height, device, expected):
    assert classify(width, height, device) is expected


def test_unknown_device():
    with pytest.raises(ValueError, match="unknown device"):
        classify(100, 100, "tablet")


def test_classify_imports_only_sizes():
    """The decision layer never touches the world. Enforced by parsing the
    imports, not by matching strings: `from os import path` would slip past
    a substring check for "import os"."""
    import paperhanger.classify as module

    from tests.conftest import assert_pure_module

    assert_pure_module(module, allowed={"sizes"})


def test_purity_helper_catches_a_from_style_import(tmp_path):
    """The case the previous substring check let through."""
    import types

    from tests.conftest import assert_pure_module

    fake = tmp_path / "impure.py"
    fake.write_text("from os import path\n")
    module = types.SimpleNamespace(__file__=str(fake))
    with pytest.raises(AssertionError, match="imports"):
        assert_pure_module(module, allowed={"sizes"})
