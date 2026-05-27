from ddtrace.profiling import _metadata


def _clear_caches():
    _metadata.get_profiler_git_commit_sha.cache_clear()
    _metadata.get_profiler_version.cache_clear()


def test_get_profiler_git_commit_sha_uses_build_metadata(monkeypatch):
    commit_sha = "ABCDEF1234567890abcdef1234567890abcdef12"

    _clear_caches()
    monkeypatch.setattr(_metadata._build_metadata, "GIT_COMMIT_SHA", commit_sha)

    assert _metadata.get_profiler_git_commit_sha() == commit_sha.lower()


def test_get_profiler_git_commit_sha_returns_none_for_missing_build_metadata(monkeypatch):
    _clear_caches()
    monkeypatch.setattr(_metadata._build_metadata, "GIT_COMMIT_SHA", "")

    assert _metadata.get_profiler_git_commit_sha() is None


def test_get_profiler_git_commit_sha_returns_none_for_invalid_build_metadata(monkeypatch):
    _clear_caches()
    monkeypatch.setattr(_metadata._build_metadata, "GIT_COMMIT_SHA", "not a sha")

    assert _metadata.get_profiler_git_commit_sha() is None


def test_get_profiler_version_includes_build_metadata_commit_sha(monkeypatch):
    commit_sha = "a" * 40

    _clear_caches()
    monkeypatch.setattr(_metadata._build_metadata, "GIT_COMMIT_SHA", commit_sha)
    monkeypatch.setattr(_metadata, "__version__", "4.10.0rc1")

    assert _metadata.get_profiler_version() == f"4.10.0rc1+g{commit_sha}"


def test_get_profiler_version_preserves_final_release_version(monkeypatch):
    _clear_caches()
    monkeypatch.setattr(_metadata._build_metadata, "GIT_COMMIT_SHA", "a" * 40)
    monkeypatch.setattr(_metadata, "__version__", "4.10.0")

    assert _metadata.get_profiler_version() == "4.10.0"


def test_get_profiler_version_preserves_version_without_build_metadata(monkeypatch):
    _clear_caches()
    monkeypatch.setattr(_metadata._build_metadata, "GIT_COMMIT_SHA", "")
    monkeypatch.setattr(_metadata, "__version__", "4.10.0rc1")

    assert _metadata.get_profiler_version() == "4.10.0rc1"
