"""Basic test to ensure the package imports correctly."""


def test_import_ddtestpy() -> None:
    """Test that the main package can be imported."""
    import ddtestpy

    assert isinstance(ddtestpy.__version__, str)
