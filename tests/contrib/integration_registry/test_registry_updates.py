from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil

import pytest
from registry_update_helpers.integration_update_orchestrator import IntegrationUpdateOrchestrator
import yaml


@pytest.fixture
def updater_project(tmp_path, project_root, registry_yaml_path):
    """Run the real updater subprocesses against an isolated copy of the registry."""
    registry_dir = tmp_path / "scripts" / "integration_registry"
    helpers_dir = registry_dir / "registry_update_helpers"
    helpers_dir.mkdir(parents=True)
    source = project_root / "scripts" / "integration_registry" / "registry_update_helpers"
    for name in ("__init__.py", "integration.py", "integration_registry_updater.py"):
        shutil.copyfile(source / name, helpers_dir / name)
    shutil.copyfile(registry_yaml_path, registry_dir / "registry.yaml")
    (tmp_path / "pyproject.toml").touch()
    return tmp_path


def _write_update(project: Path, version: str) -> Path:
    path = project / f"update-{version}.json"
    path.write_text(json.dumps({"molten": {"molten": {"version": version}}}))
    return path


def test_concurrent_registry_updates_preserve_all_integrations(updater_project, capsys, monkeypatch):
    monkeypatch.setenv("TEST_SUITE", "molten")
    registry = updater_project / "scripts" / "integration_registry" / "registry.yaml"
    original_names = {entry["integration_name"] for entry in yaml.safe_load(registry.read_text())["integrations"]}
    inputs = [_write_update(updater_project, f"1.0.{version}") for version in range(3, 11)]
    orchestrator = IntegrationUpdateOrchestrator(str(updater_project))

    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda path: orchestrator.run(str(path)), inputs))

    assert all(results), capsys.readouterr().err
    entries = yaml.safe_load(registry.read_text())["integrations"]
    assert {entry["integration_name"] for entry in entries} == original_names
    molten = next(entry for entry in entries if entry["integration_name"] == "molten")
    assert molten["tested_versions_by_dependency"]["molten"] == {"min": "1.0.2", "max": "1.0.10"}
    assert registry.with_suffix(".yaml.lock").exists()
    assert (registry.parent / "workflow.lock").exists()
    marker = updater_project / ".venv-registry-tools" / ".complete"
    completed_at = marker.stat().st_mtime_ns
    assert orchestrator.run(str(inputs[0]))
    assert marker.stat().st_mtime_ns == completed_at


@pytest.mark.parametrize("contents", ["", "integrations: [", "integrations: []", "other: []"])
def test_invalid_registry_is_not_overwritten(updater_project, contents, capsys):
    registry = updater_project / "scripts" / "integration_registry" / "registry.yaml"
    registry.write_text(contents)
    input_path = _write_update(updater_project, "1.0.3")

    assert not IntegrationUpdateOrchestrator(str(updater_project)).run(str(input_path))
    assert registry.read_text() == contents
    assert "Integration registry update failed" in capsys.readouterr().err
