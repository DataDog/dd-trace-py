import json

import filelock
import pytest
from registry_update_helpers.integration import Integration
from registry_update_helpers.integration_registry_updater import IntegrationRegistryUpdater
import yaml


LOCK_TIMEOUT_SECONDS = 0.1

REGISTRY_CONTENT = {
    "integrations": [
        {
            "integration_name": "coverage",
            "is_external_package": True,
            "is_tested": True,
            "dependency_names": ["coverage"],
            "tested_versions_by_dependency": {"coverage": {"min": "7.2.2", "max": "7.8.0"}},
        },
        {
            "integration_name": "anyio",
            "is_external_package": True,
            "is_tested": True,
            "dependency_names": ["anyio"],
            "tested_versions_by_dependency": {"anyio": {"min": "3.4.0", "max": "4.15.0"}},
        },
    ]
}


def _write_registry(updater, content=REGISTRY_CONTENT):
    updater.registry_yaml_path.write_text(yaml.dump(content, sort_keys=False))


def _write_session_data(tmp_path, data):
    data_file = tmp_path / "session_data.json"
    data_file.write_text(json.dumps(data))
    return str(data_file)


@pytest.fixture
def updater(tmp_path, monkeypatch):
    """An updater pointed at a temporary registry with short lock timeouts.

    TEST_SUITE is cleared so merge_data treats the run as a manual script
    invocation and updates version ranges for any integration in the session
    data (matching how update_and_format_registry.py behaves).
    """
    monkeypatch.delenv("TEST_SUITE", raising=False)
    instance = IntegrationRegistryUpdater()
    lock_path = tmp_path / "registry.yaml.lock"
    monkeypatch.setattr(instance, "registry_yaml_path", tmp_path / "registry.yaml")
    monkeypatch.setattr(instance, "registry_lock_path", lock_path)
    monkeypatch.setattr(instance, "lock_timeout_seconds", LOCK_TIMEOUT_SECONDS)
    monkeypatch.setattr(instance, "lock", filelock.FileLock(str(lock_path), timeout=LOCK_TIMEOUT_SECONDS))
    return instance


def test_update_preserves_unrelated_entries(updater, tmp_path):
    """A successful update must keep every entry it did not touch."""
    _write_registry(updater)
    data_file = _write_session_data(
        tmp_path,
        {"coverage": {"coverage": {"version": "7.13.1"}}},
    )

    assert updater.run(data_file)

    updated = yaml.safe_load(updater.registry_yaml_path.read_text())
    names = {entry["integration_name"] for entry in updated["integrations"]}
    assert names == {"coverage", "anyio"}
    coverage = next(e for e in updated["integrations"] if e["integration_name"] == "coverage")
    assert coverage["tested_versions_by_dependency"]["coverage"] == {"min": "7.2.2", "max": "7.13.1"}
    # The lock file's lifecycle is left to FileLock, so it persists after the run
    assert updater.registry_lock_path.exists()


def test_lock_contention_aborts_without_wiping_registry(updater, tmp_path):
    """A run that cannot acquire the registry lock must abort, not overwrite the file."""
    _write_registry(updater)
    original = updater.registry_yaml_path.read_text()
    data_file = _write_session_data(
        tmp_path,
        {"coverage": {"coverage": {"version": "7.13.1"}}},
    )

    # Simulate a concurrent updater holding the registry lock
    contention = filelock.FileLock(str(updater.registry_lock_path))
    contention.acquire()
    try:
        assert not updater.run(data_file)
    finally:
        contention.release()

    assert updater.registry_yaml_path.read_text() == original
    # The concurrent holder's lock file must survive the failed run: unlinking
    # it would let a third updater lock a fresh inode at the same path while
    # the current holder is still running.
    assert updater.registry_lock_path.exists()


def test_malformed_registry_entry_aborts_without_wiping_registry(updater, tmp_path):
    """A registry that fails to load must abort the run, not reset it to session data only."""
    malformed = {
        "integrations": [
            {
                "integration_name": "coverage",
                "is_external_package": True,
                "is_tested": True,
                "dependency_names": ["coverage"],
                "tested_versions_by_dependency": {"coverage": {"min": "7.2.2", "max": "7.8.0"}},
                "unexpected_field": True,
            }
        ]
    }
    _write_registry(updater, malformed)
    original = updater.registry_yaml_path.read_text()
    data_file = _write_session_data(
        tmp_path,
        {"coverage": {"coverage": {"version": "7.13.1"}}},
    )

    assert not updater.run(data_file)

    assert updater.registry_yaml_path.read_text() == original


def test_write_without_lock_is_refused(updater):
    """write_registry_data must never write the registry without holding the lock."""
    _write_registry(updater)
    original = updater.registry_yaml_path.read_text()

    updater.integrations["coverage"] = Integration(
        integration_name="coverage",
        is_external_package=True,
        dependency_names=["coverage"],
        tested_versions_by_dependency={"coverage": {"min": "7.2.2", "max": "7.13.1"}},
    )

    assert not updater.write_registry_data()
    assert updater.registry_yaml_path.read_text() == original


def test_up_to_date_data_releases_lock(updater, tmp_path):
    """A run with nothing to update must leave the lock released."""
    _write_registry(updater)
    data_file = _write_session_data(
        tmp_path,
        # 7.2.2 is already the recorded minimum, so no update is needed
        {"coverage": {"coverage": {"version": "7.2.2"}}},
    )

    assert not updater.run(data_file)

    assert not updater.lock.is_locked


def test_no_op_merge_releases_lock(updater, tmp_path, monkeypatch):
    """A merge that changes nothing (here: version update skipped for another
    suite's integration) must also release the lock instead of leaking it.
    """
    # Pretend we run inside a different suite than the integration being updated
    monkeypatch.setenv("TEST_SUITE", "ci_visibility::pytest")
    _write_registry(updater)
    data_file = _write_session_data(
        tmp_path,
        {"coverage": {"coverage": {"version": "7.13.1"}}},
    )

    assert not updater.run(data_file)

    assert not updater.lock.is_locked


def test_missing_registry_bootstraps_from_session_data(updater, tmp_path):
    """With no existing registry, session data seeds a fresh one."""
    data_file = _write_session_data(
        tmp_path,
        {"coverage": {"coverage": {"version": "7.13.1"}}},
    )

    assert updater.run(data_file)

    data = yaml.safe_load(updater.registry_yaml_path.read_text())
    assert data == {
        "integrations": [
            {
                "integration_name": "coverage",
                "is_external_package": True,
                "is_tested": True,
                "dependency_names": ["coverage"],
                "tested_versions_by_dependency": {"coverage": {"min": "7.13.1", "max": "7.13.1"}},
            }
        ]
    }
