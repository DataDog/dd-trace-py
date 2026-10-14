import pytest


@pytest.fixture(autouse=True)
def isolate_retry_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give in-process pytest runs a deterministic retry configuration baseline."""
    # NOTE: inline pytest runs inherit the outer worker environment, and retry
    # handlers snapshot these values during SessionManager construction. Tests that
    # exercise non-default settings override this baseline with their own monkeypatch.
    monkeypatch.delenv("DD_CIVISIBILITY_DYNAMIC_ATR_ENABLED", raising=False)
    monkeypatch.delenv("DD_CIVISIBILITY_DYNAMIC_ATR_BUCKETS", raising=False)
    monkeypatch.setenv("DD_CIVISIBILITY_EARLY_FLAKE_DETECTION_ENABLED", "true")
    monkeypatch.setenv("DD_CIVISIBILITY_FLAKY_RETRY_ENABLED", "true")
    monkeypatch.setenv("DD_CIVISIBILITY_FLAKY_RETRY_COUNT", "5")
    monkeypatch.setenv("DD_CIVISIBILITY_TOTAL_FLAKY_RETRY_COUNT", "1000")
    monkeypatch.setenv("_DD_CIVISIBILITY_OUT_OF_SESSION_RETRIES_ENABLED", "0")
