#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


SOURCE_REPOSITORY = "https://github.com/DataDog/ffe-system-test-data.git"
DESTINATION = Path("tests/openfeature/ffe-system-test-data")
SOURCE_METADATA = "SOURCE.md"
VALID_REF = re.compile(r"^[A-Za-z0-9._/-]+$")


def validate_fixture_ref(fixture_ref):
    if (
        not fixture_ref
        or fixture_ref.isspace()
        or fixture_ref.startswith("-")
        or ".." in fixture_ref
        or VALID_REF.fullmatch(fixture_ref) is None
    ):
        raise ValueError(f"Invalid FFE fixture ref: {fixture_ref}")


def run_git(working_directory, arguments, environment):
    result = subprocess.run(
        ["git", *arguments],
        cwd=working_directory,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def copy_fixture_file(source, destination):
    if source.is_symlink():
        raise ValueError(f"Refusing to copy symbolic link from FFE fixture repository: {source}")
    if not source.is_file():
        raise ValueError(f"Refusing to copy unsupported fixture entry: {source}")
    shutil.copyfile(source, destination)
    destination.chmod(0o644)


def copy_fixture_snapshot(source, snapshot):
    copy_fixture_file(source / "ufc-config.json", snapshot / "ufc-config.json")
    cases_directory = source / "evaluation-cases"
    if cases_directory.is_symlink():
        raise ValueError(f"Refusing to copy symbolic link from FFE fixture repository: {cases_directory}")
    if not cases_directory.is_dir():
        raise ValueError("FFE fixture repository does not contain the expected fixture layout")

    snapshot_cases = snapshot / "evaluation-cases"
    snapshot_cases.mkdir()
    snapshot_cases.chmod(0o755)
    for entry in sorted(cases_directory.iterdir()):
        if entry.suffix != ".json":
            raise ValueError(f"Unexpected entry in FFE evaluation cases: {entry}")
        copy_fixture_file(entry, snapshot_cases / entry.name)


def validate_fixture_snapshot(snapshot):
    config_path = snapshot / "ufc-config.json"
    cases_directory = snapshot / "evaluation-cases"
    if not config_path.is_file() or not cases_directory.is_dir():
        raise ValueError("FFE fixture repository does not contain the expected fixture layout")

    with config_path.open(encoding="utf-8") as config_file:
        json.load(config_file)

    case_files = sorted(cases_directory.glob("*.json"))
    if not case_files:
        raise ValueError("No FFE JSON fixture files found")

    fixture_count = 0
    for case_file in case_files:
        with case_file.open(encoding="utf-8") as fixture_file:
            cases = json.load(fixture_file)
        if not isinstance(cases, list):
            raise ValueError(f"{case_file} must contain a JSON array of test cases")
        fixture_count += len(cases)

    if fixture_count == 0:
        raise ValueError("No FFE fixture test cases found")

    return fixture_count


def relative_files(directory, excluded_files=frozenset()):
    files = []
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Refusing symbolic link in FFE fixture snapshot: {path}")
        if path.is_file():
            relative_path = path.relative_to(directory)
            if relative_path.as_posix() not in excluded_files:
                files.append(relative_path)
    return sorted(files)


def have_same_contents(snapshot, destination):
    if not destination.is_dir():
        return False

    snapshot_files = relative_files(snapshot)
    destination_files = relative_files(destination, frozenset({SOURCE_METADATA}))
    if snapshot_files != destination_files:
        return False

    return all(
        (snapshot / relative_path).read_bytes() == (destination / relative_path).read_bytes()
        for relative_path in snapshot_files
    )


def recorded_source_commit(destination):
    metadata_path = destination / SOURCE_METADATA
    if metadata_path.is_symlink():
        raise ValueError(f"Refusing symbolic link in FFE fixture snapshot: {metadata_path}")
    metadata = metadata_path.read_text(encoding="utf-8")
    commits = re.findall(r"^Source commit: (.*)$", metadata, re.MULTILINE)
    if len(commits) != 1 or re.fullmatch(r"[0-9a-f]{40}", commits[0]) is None:
        raise ValueError(f"{metadata_path} must record exactly one full upstream commit SHA")
    return commits[0]


def source_metadata(source_commit):
    return f"""# FFE Fixture Snapshot

These files are copied from the canonical FFE fixture repository.

Canonical source: https://github.com/DataDog/ffe-system-test-data
Source commit: {source_commit}

Do not edit these fixtures directly in dd-trace-py. Add or update shared FFE behavior
in ffe-system-test-data first, then refresh this snapshot.

The weekly update workflow runs `python scripts/update-ffe-fixtures.py` and opens a
draft test PR only when the allowed fixture contents change.
"""


def write_github_outputs(source_commit, fixture_count, changed):
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as output_file:
            output_file.write(f"source_commit={source_commit}\n")
            output_file.write(f"fixture_count={fixture_count}\n")
            output_file.write(f"changed={str(changed).lower()}\n")


def update_fixture_snapshot(repository_root, fixture_ref, *, check=False):
    destination = repository_root / DESTINATION
    if check:
        fixture_ref = recorded_source_commit(destination)
    validate_fixture_ref(fixture_ref)

    with tempfile.TemporaryDirectory(prefix="ffe-system-test-data-") as temporary_directory:
        working_directory = Path(temporary_directory)
        source = working_directory / "source"
        snapshot = working_directory / "snapshot"
        empty_git_config = working_directory / "empty-git-config"
        source.mkdir()
        snapshot.mkdir()
        snapshot.chmod(0o755)
        empty_git_config.touch()

        git_environment = os.environ.copy()
        git_environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": str(empty_git_config),
            }
        )

        run_git(source, ["init", "--quiet"], git_environment)
        run_git(source, ["remote", "add", "origin", SOURCE_REPOSITORY], git_environment)
        run_git(source, ["fetch", "--quiet", "--depth", "1", "origin", fixture_ref], git_environment)
        run_git(source, ["checkout", "--quiet", "--detach", "FETCH_HEAD"], git_environment)
        source_commit = run_git(source, ["rev-parse", "HEAD"], git_environment)
        if check and source_commit != fixture_ref:
            raise ValueError(f"Fetched FFE fixture commit does not match recorded SHA: {fixture_ref}")

        copy_fixture_snapshot(source, snapshot)
        fixture_count = validate_fixture_snapshot(snapshot)
        changed = not have_same_contents(snapshot, destination)

        if check and changed:
            raise ValueError(
                f"FFE fixture snapshot does not match SOURCE.md commit {source_commit}. "
                f"Refresh with: python scripts/update-ffe-fixtures.py --ref {source_commit}"
            )

        if changed:
            metadata_path = snapshot / SOURCE_METADATA
            metadata_path.write_text(source_metadata(source_commit), encoding="utf-8")
            metadata_path.chmod(0o644)
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(snapshot, destination)

    print(f"Checked FFE fixtures from DataDog/ffe-system-test-data@{source_commit}")
    print(f"Loaded {fixture_count} JSON fixture cases")
    print(f"Fixture snapshot changed: {str(changed).lower()}")
    if not check:
        write_github_outputs(source_commit, fixture_count, changed)


def main():
    parser = argparse.ArgumentParser(description="Update the checked-in canonical FFE fixture snapshot")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--ref", help="Branch, tag, or commit from DataDog/ffe-system-test-data")
    mode.add_argument(
        "--check", action="store_true", help="Verify the snapshot against SOURCE.md without changing files"
    )
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parent.parent
    fixture_ref = arguments.ref if arguments.ref is not None else "main"
    update_fixture_snapshot(repository_root, fixture_ref, check=arguments.check)


if __name__ == "__main__":
    main()
