import importlib.metadata

import boundary_latch


def test_version_matches_metadata():
    assert boundary_latch.__version__ == importlib.metadata.version("boundary-latch")


def test_no_heavy_imports():
    import sys

    for name in ("redline", "numpy", "torch", "sklearn", "fastapi", "httpx"):
        assert name not in sys.modules
