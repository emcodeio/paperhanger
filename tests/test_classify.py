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


def test_purity_helper_reports_the_module_not_the_imported_names(tmp_path):
    """`from .classify import CROP, classify` must record the MODULE
    `classify`, not the names `CROP` and `classify`. Branching on
    node.level instead of node.module silently inverts this."""
    import types

    from tests.conftest import assert_pure_module

    fake = tmp_path / "submodule_import.py"
    fake.write_text("from .classify import CROP, classify\n")
    module = types.SimpleNamespace(__file__=str(fake))

    assert_pure_module(module, allowed={"classify"})        # the module is allowed

    with pytest.raises(AssertionError, match="classify"):
        assert_pure_module(module, allowed={"CROP"})        # a NAME is not a module


# ---------- the four violations the import check alone let through ----------
#
# Every one of these passed the helper when it checked imports and nothing
# else, which is what made the purity assertions on classify.py and plan.py
# weaker than they read. Each case is written against `plan.py`'s own
# allow-list, because that is the module where the temptation is real: spec
# section 3 contemplated putting `destination.exists()` in the planner.

PLAN_ALLOW_LIST = {"dataclasses", "pathlib", "bands", "formats",
                   "geometry", "sizes", "classify"}


def _fake_module(tmp_path, name: str, source: str):
    import types

    path = tmp_path / name
    path.write_text(source)
    return types.SimpleNamespace(__file__=str(path))


@pytest.mark.parametrize("name,source,expected", [
    # The realistic one. `pathlib` is allowed for path ARITHMETIC, so the
    # import check cannot tell building a destination from stat-ing one.
    ("stats.py",
     "from pathlib import Path\n"
     "def done(p):\n"
     "    return Path(p).exists()\n",
     r"\.exists\(\)"),
    ("reads.py",
     "from pathlib import Path\n"
     "def load(p):\n"
     "    return Path(p).read_text()\n",
     r"\.read_text\(\)"),
    # No import at all: `open` is a builtin, so there is nothing for an
    # import check to look at.
    ("builtin_open.py",
     "def load(p):\n"
     "    return open(p).read()\n",
     r"open\(\)"),
    # The import check's own blind spot, stated outright.
    ("smuggled.py",
     "def listing(p):\n"
     "    return __import__('os').listdir(p)\n",
     r"__import__\(\)"),
])
def test_purity_helper_catches_the_call_not_only_the_import(
        tmp_path, name, source, expected):
    from tests.conftest import assert_pure_module

    module = _fake_module(tmp_path, name, source)
    with pytest.raises(AssertionError, match=expected):
        assert_pure_module(module, allowed=PLAN_ALLOW_LIST)


def test_purity_helper_still_allows_path_arithmetic(tmp_path):
    """Guards the guard: the check must not ban `pathlib` outright.

    `plan.py` builds every destination out of these six operations and must
    keep passing, or the assertion above would be satisfied by a helper that
    simply refused all of pathlib -- which would be a different rule, and one
    the code cannot live with.
    """
    from tests.conftest import assert_pure_module

    module = _fake_module(
        tmp_path, "arithmetic.py",
        "from pathlib import Path\n"
        "def destination(directory, source, suffix):\n"
        "    stem = source.stem + suffix\n"
        "    named = source.with_name(stem)\n"
        "    return Path(directory) / named.parent.name / named.name\n",
    )
    assert_pure_module(module, allowed=PLAN_ALLOW_LIST)


def test_purity_helper_does_not_fire_on_a_bare_attribute(tmp_path):
    """`.open` as a VALUE is not `.open()` as a call.

    The distinction is what lets `p.name` and `p.stem` through while
    `p.exists()` is caught, so a check that walked Attribute nodes instead of
    Call nodes would ban the arithmetic above along with the I/O.
    """
    from tests.conftest import assert_pure_module

    module = _fake_module(
        tmp_path, "attribute.py",
        "def opener(handle):\n"
        "    return handle.open\n",
    )
    assert_pure_module(module, allowed=set())


def test_purity_helper_catches_a_function_local_import(tmp_path):
    """ast.walk descends into function bodies; a check reading only
    tree.body would miss the import most likely to be added in a hurry."""
    from tests.conftest import assert_pure_module

    module = _fake_module(
        tmp_path, "local_import.py",
        "def run(argv):\n"
        "    import subprocess\n"
        "    return subprocess.run(argv)\n",
    )
    with pytest.raises(AssertionError, match="subprocess"):
        assert_pure_module(module, allowed=PLAN_ALLOW_LIST)
