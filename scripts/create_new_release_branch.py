import os
import re
import subprocess

from github import Github


"""
This script automates two steps in the release process:

- Creating the new release branch for minor versions (ex - 2.17 branch after
  2.17.0rc1 is built and published, so contributors can backport)
- Update the version_scheme in the pyproject.toml file from release-branch-semver to
  guess-next-dev. This is a required step in the release process after the first release
  candidate is triggered to ensure system tests work properly for backports.

Setup:
1. Create a Personal access token (classic), not a fine grained one, on Github:
https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token#creating-a-personal-access-token-classic
2. Give the Github token repo, user, audit_log, project permissions.
   On the next page authorize your token for Datadog SSO.
3. Add `export GH_TOKEN=<github token>` to your `.zhrc` file.


4. Create an activate a virtual environment, and install required packages :
`python -m venv venv && source venv/bin/activate && pip install pygithub`


Usage:
The script should be run from the `scripts` directory.

Required:
    BASE - The major and minor version you are creating the new release branch for (e.g. BASE=2.17)

Optional:
    DRY_RUN - When set to "1", this script does not write/push anything to github.
    It will do all steps locally so you can see what would have been pushed.

General Process:
1. Run the release.py script for the first release candidate (ex - 2.17.0rc1)
2. Once draft release notes have been approved, trigger the build and publish for the first release candidate.
3. Confirm that the release candidate tag has been created (ex - v2.17.0rc1)
4. Run this script:
   BASE=2.17 python3 create_new_release_branch.py
5. You should see that a new release branch should be created (ex - 2.17) along with a PR with the changes in the
   pyproject.toml file. Ensure this gets approved and merged before the first backport for that release is created.
"""

DEFAULT_BRANCH = "main"
BASE = os.getenv("BASE", "")
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
PYPROJECT_FILENAME = "../pyproject.toml"


def create_release_branch():
    rc1_tag = f"v{BASE}.0rc1"

    try:
        subprocess.check_call(f"git checkout {rc1_tag}", shell=True, cwd=os.pardir)
    except subprocess.CalledProcessError:
        print(f"\u274C Failed to checkout {rc1_tag} tag")
        raise

    create_new_branch(BASE)

    if DRY_RUN:
        print(
            f"DRY RUN: Would push {BASE} branch from {rc1_tag} tag to origin."
            f"You can check out what would have been pushed in your local {BASE}"
        )
    else:
        try:
            subprocess.check_call(f"git push origin {BASE}", shell=True, cwd=os.pardir)
        except subprocess.CalledProcessError as e:
            print(f"\u274C Encountered error when trying to push {BASE} branch")
            raise e

    print(f"\u2705 Checked out and pushed {BASE} branch created from {rc1_tag} commit")


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
    print("\u2705 version_scheme updated to 'guess-next-dev'.")


def create_pull_request():
    # Get GH token
    gh_token = os.getenv("GH_TOKEN")
    if not gh_token:
        raise ValueError(
            "\u274C We need a Github token to create the PR. Please set GH_TOKEN in your environment variables"
        )
    dd_repo = Github(gh_token).get_repo(full_name_or_id="DataDog/dd-trace-py")

    # Create branch
    pr_branch_name = f"script/guess-next-dev-{BASE}"

    create_new_branch(pr_branch_name)

    # Update pyproject.toml
    update_version_scheme()
    try:
        subprocess.check_output(f"git add {PYPROJECT_FILENAME}", shell=True, cwd=os.pardir)
    except subprocess.CalledProcessError:
        try:
            subprocess.check_output("git add pyproject.toml", shell=True, cwd=os.pardir)
        except subprocess.CalledProcessError:
            raise ValueError(
                f"\u274C Couldn't find the {PYPROJECT_FILENAME} file when trying to modify and create PR for it."
                "You may need to run this script from the root of the repository."
            )
    print(f"Committing changes to {PYPROJECT_FILENAME} on branch {pr_branch_name}")
    pr_title = f"chore: use guess-next-dev instead of release-branch-semver [{BASE}]"
    pr_body = (
        f"This PR updates the `version_schema` in the `{PYPROJECT_FILENAME}` file for the {BASE} release branch "
        "from `release-branch-semver` to `guess-next-dev`. This is to ensure that system tests work as intended "
        "with backports to this release branch."
        "\n\n"
        f"IMPORTANT: This PR needs to be merged before the first backport is created for {BASE}."
        "Otherwise, system tests will not work as expected."
    )
    try:
        subprocess.check_output(
            f"git commit -m 'Update version_schema for the {BASE} release branch via script'", shell=True, cwd=os.pardir
        )
    except subprocess.CalledProcessError:
        print(f"\u274C Failed to commit changes to {pr_branch_name}")
        raise
    if DRY_RUN:
        print(f"DRY RUN: Would create PR with target branch set to {pr_branch_name}")
    else:
        try:
            subprocess.check_output(f"git push origin {pr_branch_name}", shell=True, cwd=os.pardir)
        except subprocess.CalledProcessError:
            print(f"\u274C Failed to push committed changes to {pr_branch_name}")
            raise

        try:
            pr = dd_repo.create_pull(title=pr_title, body=pr_body, base=BASE, head=pr_branch_name, draft=False)
            pr.add_to_labels("changelog/no-changelog")
            print("\u2705 Created PR")
        except Exception as e:
            print(f"\u274C Failed to create PR from {pr_branch_name} into {BASE} due to: {e}")
            raise
    print("\u2705 Done")


def create_new_branch(branch_name: str):
    try:
        subprocess.check_call(f"git checkout -b {branch_name}", shell=True, cwd=os.pardir)
    except subprocess.CalledProcessError as e:
        # Capture the error message
        error_message = e.stderr.decode("utf-8") if e.stderr else str(e)
        if f"Command 'git checkout -b {branch_name}' returned non-zero exit status 128." in error_message:
            print(f"\u2705 Branch '{branch_name}' already exists. Skipping branch creation...")
            subprocess.check_call(f"git checkout {branch_name}", shell=True, cwd=os.pardir)
        else:
            print(f"\u274C Encountered error when trying to create branch {branch_name}")
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

    if BASE == "":
        raise ValueError("Need to specify the base ref with env var e.g. BASE=2.10")
    if ".x" in BASE:
        raise ValueError("Base ref must be a fully qualified semantic version.")

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
