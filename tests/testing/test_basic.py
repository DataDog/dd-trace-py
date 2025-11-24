"""Basic test to ensure the package imports correctly."""


def test_import_ddtrace_testing() -> None:
    """Test that the main package can be imported."""
    import ddtrace.testing

    assert isinstance(ddtrace.testing.__version__, str)
