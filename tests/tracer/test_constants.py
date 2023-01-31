import warnings


def test_not_deprecated():
    import ddtrace

    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")

        assert ddtrace.constants.ENV_KEY
        assert len(ws) == 0
