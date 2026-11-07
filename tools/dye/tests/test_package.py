import boundary_dye


def test_version_and_exports():
    assert boundary_dye.__version__ == "0.1.0"
    assert set(boundary_dye.__all__) == {"Finding", "Report", "Source", "Tracker", "__version__"}
