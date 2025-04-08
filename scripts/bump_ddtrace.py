#!/usr/bin/env python3
import os
import platform
import re
import subprocess
import sys

import requests


PROJECT = os.getenv("BUMP_PROJECT", "")
GH_USERNAME = os.getenv("BUMP_GH_UNAME", "")
OS_USERNAME = os.getenv("BUMP_OS_UNAME", "")


PROJECT_MAIN_BRANCH_NAME = "main"
PARTIAL_BRANCH_NAME = "bump-ddtrace-to-"
PACKAGE_NAME = "ddtrace"

linux = platform.system() == "Linux"
HOME_PREFIX = "/home" if linux else "/Users"
OPEN_CMD = "xdg-open" if linux else "open"


def run_command(command, check=True, cwd=None):
    """Utility function to run shell commands."""
    print(f"Running: {command}")
    result = subprocess.run(command, shell=True, check=check, text=True, capture_output=True, cwd=cwd)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result


def get_current_git_branch(project_path):
    """Get the name of the current git branch."""
    result = run_command("git rev-parse --abbrev-ref HEAD", check=False, cwd=project_path)
    return result.stdout.strip()


def create_git_branch(branch_name, project_path):
    """Step 0: Create a new git branch if not already on it."""
    current_branch = get_current_git_branch(project_path)
    if current_branch != branch_name:
        run_command(f"git checkout -b '{branch_name}'", cwd=project_path)


def get_latest_dependency_version(package_name):
    """Step 1: Get the latest version of a package from PyPI."""
    url = f"https://pypi.org/pypi/{package_name}/json"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json().get("info", {}).get("version")
    return None


def modify_requirements_file(requirements_path, new_version):
    """Step 2: Modify the requirements.in file."""
    print(f"Updating {PACKAGE_NAME} to version {new_version} in {requirements_path}")
    with open(requirements_path, "r") as file:
        lines = file.readlines()

    with open(requirements_path, "w") as file:
        for line in lines:
            if re.match(rf"{PACKAGE_NAME}==[0-9]+(\.[0-9]+)*", line):
                file.write(f"{PACKAGE_NAME}=={new_version}\n")
            else:
                file.write(line)


def run_bazel_commands(project_path, only_vendor=False):
    """Step 3: Run necessary Bazel commands."""
    commands = ["bzl run //:requirements.update", "bzl run //:requirements_vendor"]
    if only_vendor:
        commands = commands[1:]
    for cmd in commands:
        run_command(cmd, cwd=project_path)


def commit_and_push_changes(branch_name, project_path, latest_version):
    """Step 4: Commit the changes and push to remote."""
    run_command("git add requirements*", cwd=project_path)
    input("Are you ready to commit and push the changes? Press Enter to continue...")
    run_command(f"git commit -m 'Bump {PACKAGE_NAME} version to {latest_version}'", cwd=project_path)
    run_command(f"git push --set-upstream origin {branch_name}", cwd=project_path)


def create_bump_pr(branch_name, project_path, requirements_path, latest_version, project):
    try:
        create_git_branch(branch_name, project_path)
        modify_requirements_file("/".join((project_path, requirements_path)), latest_version)
        run_bazel_commands(project_path)
        commit_and_push_changes(branch_name, project_path, latest_version)
        run_command(f"{OPEN_CMD} https://github.com/DataDog/{project}/pull/new/{branch_name}")
    except Exception:
        run_command("git reset --hard HEAD", cwd=project_path)
        run_command(f"git checkout {PROJECT_MAIN_BRANCH_NAME}", cwd=project_path)
        run_command(f"git branch -D {branch_name}", cwd=project_path)


def update_pr(branch_name, project_path):
    # update main branch
    run_command(f"git checkout {PROJECT_MAIN_BRANCH_NAME}", cwd=project_path)
    run_command("git pull", cwd=project_path)
    # checkout branch
    run_command(f"git checkout {branch_name}", cwd=project_path)
    # save git sha
    git_sha = run_command("git rev-parse HEAD", cwd=project_path).stdout.strip()
    cherry_pick_success = False
    try:
        # reset branch to main_branch
        run_command(f"git reset --hard {PROJECT_MAIN_BRANCH_NAME}", cwd=project_path)
        # cherry-pick commit, this will fail if there are conflicts, that's OK
        result = run_command(f"git cherry-pick {git_sha}", check=False, cwd=project_path)
        if "conflict" in result.stdout.lower() or "conflict" in result.stderr.lower():
            # reset requirement_* files to main state
            run_command(f"git checkout {PROJECT_MAIN_BRANCH_NAME} -- requirements_*", cwd=project_path)
            # run bazel commands
            run_bazel_commands(project_path, only_vendor=True)
            # add new changes
            run_command("git add requirements_*", cwd=project_path)
            # cherry-pick continue with changes, no editor
            run_command("git -c core.editor=true cherry-pick --continue", check=False, cwd=project_path)
            cherry_pick_success = True
        else:
            cherry_pick_success = True

    except Exception:
        # if there are conflicts, abort the cherry-pick and reset the branch
        run_command("git cherry-pick --abort", check=False, cwd=project_path)
        run_command(f"git reset --hard {git_sha}", cwd=project_path)

    if cherry_pick_success:
        # force push changes
        run_command("git push --force", cwd=project_path)


def main(package_version=None):
    if not all((GH_USERNAME, OS_USERNAME, PROJECT)):
        print("Fill the required constants at the top of the file (project and usernames)")
        sys.exit(1)

    requirements_path = "requirements.in"
    project_path = f"{HOME_PREFIX}/{OS_USERNAME}/go/src/github.com/DataDog/{PROJECT}"

    latest_version = package_version or get_latest_dependency_version(PACKAGE_NAME)
    branch_name = "/".join((GH_USERNAME, PARTIAL_BRANCH_NAME + latest_version))

    # To decide whether if create or update the PR, check if the branch name exists in origin
    result = run_command(f"git ls-remote --heads origin {branch_name}", check=False, cwd=project_path)
    branch_exists = result.stdout

    if not branch_exists:
        create_bump_pr(branch_name, project_path, requirements_path, latest_version, PROJECT)
    else:
        update_pr(branch_name, project_path)


if __name__ == "__main__":
    # Usage: ./bump_ddtrace.py 0.1.2
    # Get package version from command line arguments
    package_version = None
    if len(sys.argv) > 1:
        package_version = sys.argv[1]
    main(package_version)
