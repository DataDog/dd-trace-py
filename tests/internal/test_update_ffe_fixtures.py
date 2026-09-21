import importlib.util
import json
from pathlib import Path
import shutil
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


def test_copy_fixture_snapshot_copies_only_config_and_case_json(updater, tmp_path):
    source = tmp_path / "source"
    snapshot = tmp_path / "snapshot"
    source.mkdir()
    snapshot.mkdir()
    write_fixture_snapshot(source)
    (source / "README.md").write_text("upstream documentation", encoding="utf-8")
    (source / "AGENTS.md").write_text("upstream contributor instructions", encoding="utf-8")
    (source / "precomputed-assignments").mkdir()
    (source / "precomputed-assignments" / "case.json").write_text("{}", encoding="utf-8")
    (source / "schemas").mkdir()
    (source / "schemas" / "precomputed-assignment.schema.json").write_text("{}", encoding="utf-8")
    (source / "ci").mkdir()
    (source / "ci" / "validate.py").write_text("raise SystemExit(1)", encoding="utf-8")
    (source / "conftest.py").write_text("# unrelated upstream file", encoding="utf-8")
    (source / "test_example.py").write_text("# unrelated upstream file", encoding="utf-8")
    (source / "other.json").write_text("{}", encoding="utf-8")

    updater.copy_fixture_snapshot(source, snapshot)

    assert updater.relative_files(snapshot) == [Path("evaluation-cases/cases.json"), Path("ufc-config.json")]
    assert (snapshot / "ufc-config.json").stat().st_mode & 0o777 == 0o644
    assert (snapshot / "evaluation-cases").stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize("entry", ["README.md", "conftest.py", "test_example.py", "nested.json"])
def test_copy_fixture_snapshot_rejects_unexpected_case_entries(updater, tmp_path, entry):
    source = tmp_path / "source"
    snapshot = tmp_path / "snapshot"
    write_fixture_snapshot(source)
    snapshot.mkdir()
    unexpected = source / "evaluation-cases" / entry
    if entry == "nested.json":
        unexpected.mkdir()
    else:
        unexpected.write_text("# unrelated upstream file", encoding="utf-8")

    with pytest.raises(ValueError, match="Unexpected entry|unsupported fixture entry"):
        updater.copy_fixture_snapshot(source, snapshot)


@pytest.mark.parametrize("entry", ["ufc-config.json", "evaluation-cases", "evaluation-cases/cases.json"])
def test_copy_fixture_snapshot_rejects_symlinks(updater, tmp_path, entry):
    source = tmp_path / "source"
    snapshot = tmp_path / "snapshot"
    write_fixture_snapshot(source)
    snapshot.mkdir()
    original = source / entry
    target = tmp_path / "target"
    original.rename(target)
    original.symlink_to(target, target_is_directory=target.is_dir())

    with pytest.raises(ValueError, match="symbolic link"):
        updater.copy_fixture_snapshot(source, snapshot)


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


@pytest.fixture
def fixture_checkout(updater, tmp_path, monkeypatch):
    source_commit = "a" * 40
    upstream = tmp_path / "upstream"
    write_fixture_snapshot(upstream)
    repository = tmp_path / "repository"
    destination = repository / updater.DESTINATION
    write_fixture_snapshot(destination)
    (destination / updater.SOURCE_METADATA).write_text(updater.source_metadata(source_commit), encoding="utf-8")
    fetched_refs = []

    def run_git(working_directory, arguments, environment):
        if arguments[0] == "fetch":
            fetched_refs.append(arguments[-1])
        elif arguments[0] == "checkout":
            shutil.copytree(upstream, working_directory, dirs_exist_ok=True)
        elif arguments[0] == "rev-parse":
            return source_commit
        return ""

    monkeypatch.setattr(updater, "run_git", run_git)
    return repository, fetched_refs


def snapshot_state(destination):
    return {
        path.relative_to(destination): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in destination.rglob("*")
        if path.is_file()
    }


def test_check_uses_recorded_commit_without_writing(updater, fixture_checkout, tmp_path, monkeypatch):
    repository, fetched_refs = fixture_checkout
    destination = repository / updater.DESTINATION
    before = snapshot_state(destination)
    github_output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))

    updater.update_fixture_snapshot(repository, "main", check=True)

    assert fetched_refs == ["a" * 40]
    assert snapshot_state(destination) == before
    assert not github_output.exists()


@pytest.mark.parametrize("change", ["modified", "missing", "extra"])
def test_check_rejects_drift_without_repairing_it(updater, fixture_checkout, change):
    repository, _ = fixture_checkout
    destination = repository / updater.DESTINATION
    if change == "modified":
        (destination / "evaluation-cases" / "cases.json").write_text('[{"flag": "changed"}]', encoding="utf-8")
    elif change == "missing":
        (destination / "evaluation-cases" / "cases.json").unlink()
    else:
        (destination / "extra.json").write_text("{}", encoding="utf-8")
    before = snapshot_state(destination)

    with pytest.raises(ValueError, match="snapshot does not match SOURCE.md commit"):
        updater.update_fixture_snapshot(repository, "main", check=True)

    assert snapshot_state(destination) == before


@pytest.mark.parametrize("commit", ["main", "a" * 7, "g" * 40, "a" * 40 + "\nSource commit: " + "b" * 40])
def test_check_rejects_invalid_source_metadata_before_fetching(updater, fixture_checkout, commit):
    repository, fetched_refs = fixture_checkout
    destination = repository / updater.DESTINATION
    (destination / updater.SOURCE_METADATA).write_text(f"Source commit: {commit}\n", encoding="utf-8")
    before = snapshot_state(destination)

    with pytest.raises(ValueError, match="exactly one full upstream commit SHA"):
        updater.update_fixture_snapshot(repository, "main", check=True)

    assert fetched_refs == []
    assert snapshot_state(destination) == before


def test_check_requires_source_metadata(updater, fixture_checkout):
    repository, fetched_refs = fixture_checkout
    (repository / updater.DESTINATION / updater.SOURCE_METADATA).unlink()

    with pytest.raises(FileNotFoundError):
        updater.update_fixture_snapshot(repository, "main", check=True)

    assert fetched_refs == []


def test_update_repairs_drift_and_then_preserves_unchanged_snapshot(updater, fixture_checkout):
    repository, fetched_refs = fixture_checkout
    destination = repository / updater.DESTINATION
    (destination / "ufc-config.json").write_text('{"changed": true}', encoding="utf-8")

    updater.update_fixture_snapshot(repository, "main")

    assert (destination / "ufc-config.json").read_text(encoding="utf-8") == "{}"
    assert updater.recorded_source_commit(destination) == "a" * 40
    before = snapshot_state(destination)

    updater.update_fixture_snapshot(repository, "main")

    assert fetched_refs == ["main", "main"]
    assert snapshot_state(destination) == before


@pytest.mark.parametrize("fixture_ref", ["main", "reviewed-ref"])
def test_check_cannot_override_recorded_commit(updater, monkeypatch, fixture_ref):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT_PATH), "--check", "--ref", fixture_ref])
    monkeypatch.setattr(updater, "update_fixture_snapshot", lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as error:
        updater.main()

    assert error.value.code == 2


@pytest.mark.parametrize(
    "arguments,expected_ref,expected_check",
    [([], "main", False), (["--ref", "reviewed-ref"], "reviewed-ref", False), (["--check"], "main", True)],
)
def test_main_selects_fixture_mode(updater, monkeypatch, arguments, expected_ref, expected_check):
    calls = []
    monkeypatch.setattr(sys, "argv", [str(SCRIPT_PATH), *arguments])
    monkeypatch.setattr(
        updater, "update_fixture_snapshot", lambda root, ref, *, check: calls.append((root, ref, check))
    )

    updater.main()

    assert calls == [(SCRIPT_PATH.parent.parent, expected_ref, expected_check)]


def test_main_rejects_empty_ref(updater, monkeypatch):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT_PATH), "--ref", ""])

    with pytest.raises(ValueError, match="Invalid FFE fixture ref"):
        updater.main()
