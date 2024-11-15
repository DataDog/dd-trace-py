from collections import namedtuple
import os
import re
import subprocess
import toml

from github import Commit
from github import Github
from github import GithubException
from packaging.version import Version


"""This release notes script is built to create a release notes draft
for release candidates, patches, and minor releases.

Setup:
1. Create a Personal access token (classic), not a fine grained one, on Github:
https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token#creating-a-personal-access-token-classic
2. Give the Github token repo, user, audit_log, project permissions. On the next page authorize your token for Datadog SSO.
3. Add `export GH_TOKEN=<github token>` to your `.zhrc` file.

4. Get API key and Application key for staging: https://ddstaging.datadoghq.com/organization-settings/api-keys
5. Add export DD_API_KEY_STAGING=<api_key> and  export DD_APP_KEY_STAGING=<app_key> to your `.zhrc` file.

6. Install pandoc with `brew install pandoc`

Create an activate a virtual environment, and install required packages :
`python -m venv venv && source venv/bin/activate && pip install pygithub requests datadog-api-client reno`


Usage:
The script should be run from the `scripts` directory.

Required:
    BASE - The base git ref you are building your release candidate, patch, or minor release off of.
        If this is a rc1, then specify the branch you'll create after the release is published. e.g. BASE=2.9

Optional:
    RC - Whether or not this is a release candidate. e.g. RC=1 or RC=0
    PATCH - Whether or not this a patch release. e.g. PATCH=1 or PATCH=0
    PRINT - Whether or not the release notes should be printed to CLI or be used to create a Github release. Default is 0 e.g. PRINT=1 or PRINT=0
    NOTEBOOK - Whether or not to create a notebook in staging. Note this only works for RC1s since those are usually what we create notebooks for.
    Default is 1 for RC1s, 0 for everything else e.g. NOTEBOOK=0 or NOTEBOOK=1
    DRY_RUN - When set to "1", this script does not write anything to github. This can be useful for development testing.
Examples:
Generate release notes and staging testing notebook for next release candidate version of 2.11: `BASE=2.11 RC=1 python release.py`

Generate release notes for next patch version of 2.13: `BASE=2.13 PATCH=1 python release.py`

Generate release notes for the 2.15 release: `BASE=2.15 python release.py`
"""

DEFAULT_BRANCH = "main"
BASE = os.getenv("BASE", "")
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
PYPROJECT_FILENAME = "../pyproject.toml"


def create_release_branch():
    rc1_tag = f"v{BASE}.0rc1"
    if DRY_RUN:
        print(f"Would create {BASE} branch from {rc1_tag} tag")
    else:
        try:
            subprocess.check_call(f"git checkout {rc1_tag}", shell=True, cwd=os.pardir)
        except subprocess.CalledProcessError:
            print(f"Failed to checkout {rc1_tag} tag")
            raise

        create_new_branch(BASE)
            
        try:
            subprocess.check_call(f"git push origin {BASE}", shell=True, cwd=os.pardir)
        except subprocess.CalledProcessError as e:
            print(f"Encountered error when trying to push {BASE} branch")
            raise e

    print(f"Checked out and pushed {BASE} branch created from {rc1_tag} commit")


def update_version_scheme():
    with open(PYPROJECT_FILENAME, "r") as f:
        content = f.read()

    # Replace the version scheme value
    content = re.sub(
        r'version_scheme\s*=\s*".*?"',
        'version_scheme = "guess-next-dev"',
        content,
    )
    with open(PYPROJECT_FILENAME, "w") as f:
        f.write(content)
    print("version_scheme updated to 'guess-next-dev'.")


def create_pull_request():
    # Get GH token
    gh_token = os.getenv("GH_TOKEN")
    if not gh_token:
        raise ValueError(
            "We need a Github token to create the PR. Please set GH_TOKEN in your environment variables"
        )
    dd_repo = Github(gh_token).get_repo(full_name_or_id="DataDog/dd-trace-py")

    # Update pyproject.toml
    update_version_scheme()

    # Create branch
    branch_name = f"script/guess-next-dev-{BASE}"
    if DRY_RUN:
        print(f"Would create PR with target branch set to {branch_name}")
    else:
        create_new_branch(branch_name)
        try:
            subprocess.check_output(f"git add {PYPROJECT_FILENAME}", shell=True, cwd=os.pardir)
        except subprocess.CalledProcessError as e:
            try:
                subprocess.check_output(f"git add pyproject.toml", shell=True, cwd=os.pardir)
            except subprocess.CalledProcessError:
                raise ValueError(
                    f"Couldn't find the {PYPROJECT_FILENAME} file when trying to modify and create PR for it."
                    "You may need to run this script from the root of the repository."
                )
        print(f"Committing changes to {PYPROJECT_FILENAME} on branch {branch_name}")
        pr_title = (
            f"use guess-next-dev instead of release-branch-semver [{BASE}]"
        )
        pr_body = (
            f"This PR updates the `version_schema` in the `{PYPROJECT_FILENAME}` file for the {BASE} release branch "
            "from `release-branch-semver` to `guess-next-dev`. This is to ensure that system tests work as intended "
            "with backports to this release branch."
            "\n\n"
            f"IMPORTANT: This PR needs to be merged before the first backport is created for {BASE}."
            "Otherwise, system tests will not work as expected.")
        try:
            subprocess.check_output(f"git commit -m 'Update version_schema for the {BASE} release branch via script'", shell=True, cwd=os.pardir)
            subprocess.check_output(f"git push origin {BASE}", shell=True, cwd=os.pardir)
        except subprocess.CalledProcessError:
            print(f"Failed to commit and push changes to {branch_name}")
            raise

        try:
            dd_repo.create_pull(title=pr_title, body=pr_body, base=BASE, head=branch_name, draft=False)
        except:
            print(f"Failed to create PR from {branch_name} into {BASE}")
            raise


def create_new_branch(branch_name: str):
    try:
        subprocess.check_call(f"git checkout -b {branch_name}", shell=True, cwd=os.pardir)
    except subprocess.CalledProcessError as e:
        # Capture the error message
        error_message = e.stderr.decode("utf-8") if e.stderr else str(e)
        if f"Command 'git checkout -b {branch_name}' returned non-zero exit status 128." in error_message:
            print(f"Branch '{branch_name}' already exists. Skipping branch creation...")
            subprocess.check_call(f"git checkout {branch_name}", shell=True, cwd=os.pardir)
        else:
            print(f"Encountered error when trying to create branch {branch_name}")
            raise e

if __name__ == "__main__":
    subprocess.check_output(
        "git stash",
        shell=True,
        cwd=os.pardir,
    )
    start_branch = (
        subprocess.check_output(
            "git rev-parse --abbrev-ref HEAD",
            shell=True,
            cwd=os.pardir,
        )
        .decode()
        .strip()
    )

    # if BASE == "":
    #     raise ValueError("Need to specify the base ref with env var e.g. BASE=2.10")
    # if ".x" in BASE:
    #     raise ValueError("Base ref must be a fully qualified semantic version.")

    create_release_branch()
    create_pull_request()

    # switch back to original git branch
    subprocess.check_output(
        "git checkout {start_branch}".format(start_branch=start_branch),
        shell=True,
        cwd=os.pardir,
    )
    print(
        (
            "\nYou've been switched back to your original branch, if you had uncommitted changes before"
            "running this command, run `git stash pop` to get them back."
        )
    )
