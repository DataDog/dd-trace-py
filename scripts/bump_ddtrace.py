#!/usr/bin/env python3
import re
import subprocess
import sys

import requests

PROJECT = ""
GH_USERNAME = ""
OS_USERNAME = ""


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

def modify_requirements_file(requirements_path, package_name, new_version):
    """Step 2: Modify the requirements.in file."""
    print(f"Updating {package_name} to version {new_version} in {requirements_path}")
    with open(requirements_path, "r") as file:
        lines = file.readlines()

    with open(requirements_path, "w") as file:
        for line in lines:
            if re.match(rf"{package_name}==[0-9]+(\.[0-9]+)*", line):
                file.write(f"{package_name}=={new_version}\n")
            else:
                file.write(line)

def run_bazel_commands(project_path):
    """Step 3: Run necessary Bazel commands."""
    commands = [
        "bzl run //:requirements.update",
        "bzl run //:requirements_vendor"
    ]
    for cmd in commands:
        run_command(cmd, cwd=project_path)

def commit_and_push_changes(branch_name, package_name, project_path):
    """Step 4: Commit the changes and push to remote."""
    run_command("git add requirements*", cwd=project_path)
    run_command(f"git commit -m 'Bump {package_name} version'", cwd=project_path)
    run_command(f"git push --set-upstream origin {branch_name}", cwd=project_path)


def create_bump_pr(branch_name, package_name, project_path, requirements_path, latest_version, project):
    create_git_branch(branch_name, project_path)
    modify_requirements_file("/".join((project_path, requirements_path)), package_name, latest_version)
    run_bazel_commands(project_path)
    commit_and_push_changes(branch_name, package_name, project_path)
    run_command(f"open https://github.com/DataDog/{project}/pull/new/{branch_name}")


def update_pr(branch_name, project_path):
    main_branch_name = "main"
    # update main branch
    run_command(f"git checkout {main_branch_name}", cwd=project_path)
    run_command(f"git pull", cwd=project_path)
    # checkout branch
    run_command(f"git checkout {branch_name}", cwd=project_path)
    # save git sha
    git_sha = run_command("git rev-parse HEAD", cwd=project_path).stdout.strip()
    # reset branch to main_branch
    run_command(f"git reset --hard {main_branch_name}", cwd=project_path)
    # cherry-pick commit
    run_command(f"git cherry-pick {git_sha}", cwd=project_path)
    # reset requirement_* files to main state
    run_command(f"git checkout {main_branch_name} -- requirements_*", cwd=project_path)
    # run bazel commands
    run_bazel_commands(project_path)
    # add new changes
    run_command("git add requirements_*", cwd=project_path)
    # cherry-pick continue with changes, no editor
    run_command("git cherry-pick --continue --no-edit", cwd=project_path)
    # force push changes
    run_command("git push --force", cwd=project_path)


def main():
    if not all((GH_USERNAME, OS_USERNAME, PROJECT)):
        print("Fill the required constants at the top of the file (project and usernames)")
        sys.exit(1)

    partial_branch_name = "bump-ddtrace-to-"
    package_name = "ddtrace"
    requirements_path = "requirements.in"
    project_path = f"/Users/{OS_USERNAME}/go/src/github.com/DataDog/{PROJECT}"

    latest_version = get_latest_dependency_version(package_name)
    branch_name = "/".join((GH_USERNAME, partial_branch_name + latest_version))

    # To decide wether if create or update the PR, check if the branch name exists in origin
    branch_exists = run_command(f"git ls-remote --heads origin {branch_name}", check=False, cwd=project_path).returncode == 0
    create_pr = not branch_exists

    if create_pr:
        create_bump_pr(branch_name, package_name, project_path, requirements_path, latest_version, PROJECT)
    else:
        update_pr(branch_name, project_path)


if __name__ == "__main__":
    main()
