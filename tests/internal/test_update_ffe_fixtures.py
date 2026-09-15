import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "update-ffe-fixtures.py"


@pytest.fixture(scope="module")
def updater():
    spec = importlib.util.spec_from_file_location("update_ffe_fixtures", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_fixture_snapshot(directory, cases=None):
    (directory / "evaluation-cases").mkdir(parents=True)
    (directory / "ufc-config.json").write_text("{}", encoding="utf-8")
    (directory / "evaluation-cases" / "cases.json").write_text(
        json.dumps([{"flag": "flag-a"}] if cases is None else cases),
        encoding="utf-8",
    )


@pytest.mark.parametrize("fixture_ref", ["main", "feature/fixtures", "v1.2.3", "ea8b5cc5"])
def test_validate_fixture_ref_accepts_git_refs(updater, fixture_ref):
    updater.validate_fixture_ref(fixture_ref)


@pytest.mark.parametrize("fixture_ref", ["", " ", "--upload-pack=evil", "main..other", "main;echo"])
def test_validate_fixture_ref_rejects_unsafe_values(updater, fixture_ref):
    with pytest.raises(ValueError, match="Invalid FFE fixture ref"):
        updater.validate_fixture_ref(fixture_ref)


def test_copy_fixture_snapshot_obeys_consumer_disallow_list(updater, tmp_path):
    source = tmp_path / "source"
    snapshot = tmp_path / "snapshot"
    source.mkdir()
    snapshot.mkdir()
    write_fixture_snapshot(source)
    (source / "README.md").write_text("upstream documentation", encoding="utf-8")
    (source / "ci").mkdir()
    (source / "ci" / "validate.py").write_text("raise SystemExit(1)", encoding="utf-8")

    updater.copy_fixture_snapshot(source, snapshot)

    assert (snapshot / "ufc-config.json").is_file()
    assert (snapshot / "evaluation-cases" / "cases.json").is_file()
    assert (snapshot / "ufc-config.json").stat().st_mode & 0o777 == 0o644
    assert (snapshot / "evaluation-cases").stat().st_mode & 0o777 == 0o755
    assert not (snapshot / "README.md").exists()
    assert not (snapshot / "ci").exists()


def test_validate_fixture_snapshot_counts_cases(updater, tmp_path):
    write_fixture_snapshot(tmp_path, cases=[{"flag": "flag-a"}, {"flag": "flag-b"}])

    assert updater.validate_fixture_snapshot(tmp_path) == 2


def test_have_same_contents_ignores_generated_source_metadata(updater, tmp_path):
    snapshot = tmp_path / "snapshot"
    destination = tmp_path / "destination"
    write_fixture_snapshot(snapshot)
    write_fixture_snapshot(destination)
    (destination / "SOURCE.md").write_text("generated metadata", encoding="utf-8")

    assert updater.have_same_contents(snapshot, destination)

    (destination / "ufc-config.json").write_text('{"changed": true}', encoding="utf-8")
    assert not updater.have_same_contents(snapshot, destination)
