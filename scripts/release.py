import json
import os
import re
import subprocess

from dotenv import load_dotenv
from github import Github


"""This release notes script is built to create a release notes draft for release candidates, patches, and minor releases.

Setup:
1. Create a `.env` file in the scripts directory.
2. Create Github token: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token#creating-a-personal-access-token-classic
3. Give the Github token repo, user, audit_log, and project permissions.
2. Add `export GH_TOKEN=<github token>` to the `.env` file.

Usage:
Required:
    BASE - The base branch you are building your release candidate, patch, or minor release off of.
        If this is a rc1, then just specify the branch you'll create after the release is published. e.g. BASE=1.9

Optional:
    RC - Whether or not this is a release candidate. e.g. RC=1 or RC=0
    PATCH - Whether or not this a patch release. e.g. PATCH=1 or PATCH=0
    
Examples:
Generate release notes for next release candidate version of 1.11: `BASE=1.11 RC=1 python release.py`

Generate release notes for next patch version of 1.13: `BASE=1.13 PATCH=1 python release.py`

Generate release notes for the 1.15 release: `BASE=1.15 python release.py`
"""

home_dir = os.path.expanduser('~')
env_loaded = load_dotenv(dotenv_path="%s/.env" % home_dir)
if not env_loaded:
    raise ValueError("No envars were loaded from .env file. Please follow the instructions in the script.")


def create_release_draft():
    #  Figure out the version of the library that you’re working on releasing grabbed with VERSION envar
    base = os.getenv("BASE")
    gh_token = os.getenv("GH_TOKEN")
    rc = bool(os.getenv("RC"))
    patch = bool(os.getenv("PATCH"))

    if base is None:
        raise ValueError("Need to specify the base version with envar e.g. BASE=1.10.0")

    # make sure we're up to date
    subprocess.run("git fetch", shell=True, cwd=os.pardir)

    # setup gh
    g = Github(gh_token)
    dd_repo = g.get_repo(full_name_or_id="DataDog/dd-trace-py")

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
        rn_raw = generate_rn(branch)
        
        unreleased = rn_raw.decode().split("## v")[0].replace("\n## Unreleased\n", "", 1).replace("# Release Notes\n", "", 1)
        unreleased = unreleased.split("###")
        unreleased_sections = dict(section.split("\n\n-") for section in unreleased)
        relevent_rns = []
        if unreleased_sections:
            relevent_rns.append(unreleased_sections)
            
        rns = rn_raw.decode().split("## v")
        split_str = "## v%s" % base
        for rn in rns:
            if rn.startswith("%s.0" % base):
                # cut out the version section
                sections = rn.split("###")[1:]
                sections_dict = dict(section.split("\n\n-") for section in sections)
                relevent_rns.append(sections_dict)

        import pdb; pdb.set_trace()
        keys = set().union(*relevent_rns)
        rn_clean = {k: "".join(dic.get(k, '') for dic in dicts)  for k in keys}
        # combine the release notes sections
            
        
        def func(*dicts):
    keys = set().union(*dicts)
    return {k: "".join(dic.get(k, '') for dic in dicts)  for k in keys}

    create_draft_release(branch=branch, name=name, tag=tag, dd_repo=dd_repo)


def clean_rn(rn_raw):
    rn = rn_raw.decode().split("## v")[0].replace("\n## Unreleased\n", "", 1).replace("# Release Notes\n", "", 1)
    return rn


def generate_rn(branch):

    subprocess.run(
        "git checkout {branch} && \
            git pull origin {branch}".format(
            branch=branch
        ),
        shell=True,
        cwd=os.pardir,
    )

    rn_raw = subprocess.check_output(
        "reno report --no-show-source | \
            pandoc -f rst -t gfm --wrap=none",
        shell=True,
        cwd=os.pardir,
    )
    return rn_raw


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
