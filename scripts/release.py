import json
import os
import re
import subprocess

from dotenv import load_dotenv
from github import Github
from github.GithubException import GithubException
from github.GithubException import UnknownObjectException


"""This release notes script is built to create a release notes draft for release candidates, patches, and minor releases.

Setup:


Usage:
Required:
    BASE - The base branch you are building your release candidate, patch, or minor release off of.
        If this is a rc1, then just specify the branch you'll create after the release is published. e.g. BASE=1.9

Optional:
    RC - Whether or not this is a release candidate. e.g. RC=1 or RC=0
    PATCH - Whether or not this a patch release. e.g. PATCH=1 or PATCH=0
"""


load_dotenv(dotenv_path=".env")


def create_release_draft():
    #  Figure out the version of the library that you’re working on releasing grabbed with VERSION envar
    base = os.getenv("BASE")
    gh_token = os.getenv("GH_TOKEN")
    rc = bool(os.getenv("RC"))
    patch = bool(os.getenv("PATCH"))
    branch_exists = None

    if base is None:
        raise ValueError("need to specify the base version with envar e.g. BASE=1.10.0")

    # make sure we're up to date
    subprocess.run("git fetch", shell=True)

    # setup gh
    g = Github(gh_token)
    dd_repo = g.get_repo(full_name_or_id="DataDog/dd-trace-py")
    try:
        # if there is a branch it means either you want a patch, an RC+1, or a final draft
        # if there is no branch, it means you want an RC
        main_branch = dd_repo.get_branch(branch=base)
        branch_exists = True
    except GithubException:
        print("No branch detected. RC release notes will be published")
        rc = True
        branch_exists = False

    if rc:
        # figure out the rc version we want
        search = "v%s.0\.?rc((\d+$))" % base
        tags = dd_repo.get_tags()
        rc_version = 0
        for tag in tags:
            try:
                rc_num = re.findall(search, tag.name)[0][0]
                rc_num = int(rc_num)
            except (IndexError, ValueError, TypeError):
                continue
            if rc_num > rc_version:
                rc_version = rc_num

        #  if this is the first rc for this base, we want to target 1.x
        if rc_version == 0:
            name = "%s.0rc1" % base
            tag = "v%s" % name
            branch = "1.x"
        # if this is the rc+1 for this base
        else:
            name = "%s.0rc%s" % (base, str(rc_version + 1))
            tag = "v%s" % name
            branch = base

    # patch release
    elif patch:
        # figure out the patch version we want
        search = "v%s.((\d+))" % base
        tags = dd_repo.get_tags()
        patch_version = 1
        for tag in tags:
            try:
                patch_num = re.findall(search, tag.name)[0][0]
                patch_num = int(patch_num)
            except (IndexError, ValueError, TypeError):
                continue
            if patch_num > patch_version:
                patch_version = patch_num

        name = "%s.%s" % (base, str(patch_version + 1))
        tag = "v%s" % name
        branch = base

    # minor release
    else:
        name = "%s.0" % base
        tag = "v%s" % name
        branch = base

    create_draft_release(branch=branch, name=name, tag=tag, dd_repo=dd_repo)


def clean_rn(rn_raw):
    rn = rn_raw.decode().split("## v")[0].replace("\n## Unreleased\n", "", 1).replace("# Release Notes\n", "", 1)
    return rn


def generate_rn(branch):
    subprocess.check_output(
        "git checkout {branch} && \
            git pull origin {branch} && \
            reno report --no-show-source | \
            pandoc -f rst -t gfm --wrap=none".format(
            branch=branch
        ),
        shell=True,
        cwd=os.pardir,
    )


def create_draft_release(branch, name, tag, dd_repo):

    rn_raw = generate_rn(branch)
    rn = clean_rn(rn_raw)

    base_branch = dd_repo.get_branch(branch=branch)
    dd_repo.create_git_release(
        name=name, tag=tag, prerelease=True, draft=True, target_commitish=base_branch, message=rn
    )


if __name__ == "__main__":
    create_release_draft()
    print("Please review your release notes draft here: https://github.com/DataDog/dd-trace-py/releases")
