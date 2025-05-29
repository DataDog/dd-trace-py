#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///

import subprocess


system_tests_repo = "https://github.com/DataDog/system-tests.git"
system_tests_workflows_path = ".github/workflows/system-tests.yml"


def get_latest_system_tests_version() -> str:
    result = subprocess.check_output(["git", "ls-remote", system_tests_repo, "refs/heads/main"])
    commit_hash, _, _ = result.decode("utf-8").partition("\t")
    return commit_hash


def get_current_system_tests_version() -> str:
    with open(system_tests_workflows_path, "r") as file:
        content = file.read()

    parse_ref = False
    lines = content.splitlines()
    for line in lines:
        if "repository: 'DataDog/system-tests'" in line:
            parse_ref = True
        if parse_ref and line.strip().startswith("ref:"):
            _, _, ref_value = line.partition(":")
            return ref_value.strip().strip("'\r\n")
    raise ValueError(f"Could not find the current system-tests version in {system_tests_workflows_path}.")


def update_system_tests_version(latest_version: str):
    with open(system_tests_workflows_path, "r") as file:
        content = file.read()

    lines = content.splitlines()
    update_ref = False
    for i in range(len(lines)):
        # Only update the ref if the repository is DataDog/system-tests
        if "repository: 'DataDog/system-tests'" in lines[i]:
            update_ref = True
        if update_ref and lines[i].strip().startswith("ref:"):
            pre, _, _ = lines[i].partition(":")
            lines[i] = f"{pre}: '{latest_version}'"
            update_ref = False

    with open(system_tests_workflows_path, "w") as file:
        file.write("\n".join(lines))


if __name__ == "__main__":
    current_version = get_current_system_tests_version()
    print(f"Current system-tests version: {current_version}")
    latest_version = get_latest_system_tests_version()
    print(f"Latest system-tests version: {latest_version}")
    update_system_tests_version(latest_version)
    print(f"Updated {system_tests_workflows_path} with the latest version.")
