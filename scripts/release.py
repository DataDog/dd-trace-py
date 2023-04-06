import os
import re
import subprocess

from github import Github


"""This release notes script is built to create a release notes draft 
for release candidates, patches, and minor releases.

Setup:
1.Create Github token: 
https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token#creating-a-personal-access-token-classic # noqa
2. Give the Github token repo, user, audit_log, and project permissions.
3. Add `export GH_TOKEN=<github token>` to your `.zhrc` file.

Usage:
Required:
    BASE - The base branch you are building your release candidate, patch, or minor release off of.
        If this is a rc1, then just specify the branch you'll create after the release is published. e.g. BASE=1.9

Optional:
    RC - Whether or not this is a release candidate. e.g. RC=1 or RC=0
    PATCH - Whether or not this a patch release. e.g. PATCH=1 or PATCH=0
    PRINT - Whether or not the release notes should be printed to CLI or be used to create a Github release. Default is 0 e.g. PRINT=1 or PRINT=0    
Examples:
Generate release notes for next release candidate version of 1.11: `BASE=1.11 RC=1 python release.py`

Generate release notes for next patch version of 1.13: `BASE=1.13 PATCH=1 python release.py`

Generate release notes for the 1.15 release: `BASE=1.15 python release.py`
"""


def create_release_draft():
    #  Figure out the version of the library that you’re working on releasing grabbed with VERSION envar
    base = os.getenv("BASE")
    gh_token = os.getenv("GH_TOKEN")
    rc = bool(os.getenv("RC"))
    patch = bool(os.getenv("PATCH"))

    if not gh_token:
        raise ValueError(
            "We need a Github token to generate the release notes. Please follow the instructions in the script."
        )

    if base is None:
        raise ValueError("Need to specify the base version with envar e.g. BASE=1.10")

    # make sure we're up to date
    subprocess.run("git fetch", shell=True, cwd=os.pardir)

    # setup gh
    g = Github(gh_token)
    dd_repo = g.get_repo(full_name_or_id="DataDog/dd-trace-py")

    if rc:
        # figure out the rc version we want
        search = r"v%s.0\.?rc((\d+$))" % base
        tags = dd_repo.get_tags()
        latest_rc_version = 0
        for tag in tags:
            try:
                other_rc_num = re.findall(search, tag.name)[0][0]
                other_rc_num = int(other_rc_num)
            except (IndexError, ValueError, TypeError):
                continue
            if other_rc_num > latest_rc_version:
                latest_rc_version = other_rc_num
        new_rc_version = latest_rc_version + 1
        #  if this is the first rc for this base, we want to target 1.x
        if new_rc_version == 1:
            name = "%s.0rc1" % base
            tag = "v%s" % name
            branch = "1.x"
        # if this is the rc+1 for this base
        else:
            name = "%s.0rc%s" % (base, str(new_rc_version))
            tag = "v%s" % name
            branch = base
        rn_raw = generate_rn(branch)
        rn = clean_rn(rn_raw)
        create_draft_release(branch=branch, name=name, tag=tag, dd_repo=dd_repo, rn=rn, prerelease=True)

    # patch release
    elif patch:
        # figure out the patch version we want
        search = r"v%s.((\d+))" % base
        tags = dd_repo.get_tags()
        latest_patch_version = 1
        for tag in tags:
            try:
                other_patch_num = re.findall(search, tag.name)[0][0]
                other_patch_num = int(other_patch_num)
            except (IndexError, ValueError, TypeError):
                continue
            if other_patch_num > latest_patch_version:
                latest_patch_version = other_patch_num
        new_patch_version = latest_patch_version + 1

        name = "%s.%s" % (base, str(new_patch_version))
        tag = "v%s" % name
        rn_raw = generate_rn(base)
        rn = clean_rn(rn_raw)
        create_draft_release(branch=base, name=name, tag=tag, dd_repo=dd_repo, rn=rn, prerelease=False)

    # official minor release
    else:
        name = "%s.0" % base
        tag = "v%s" % name
        branch = base

        rn_raw = generate_rn(branch)
        rn_sections_clean = create_release_notes_sections(rn_raw, branch)
        # combine the release note sections into a string in the correct order
        rn_clean = ""
        rn_key_order = [
            "Prelude",
            "New Features",
            "Known Issues",
            "Upgrade Notes",
            "Deprecation Notes",
            "Bug Fixes",
            "Other Changes",
        ]
        for key in rn_key_order:
            try:
                rn_clean += "### %s\n\n%s" % (key, rn_sections_clean[key])
            except KeyError:
                continue

        create_draft_release(branch=branch, name=name, tag=tag, dd_repo=dd_repo, rn=rn_clean, prerelease=False)


def clean_rn(rn_raw):
    # remove all release notes generated,
    # except for those that haven't been released yet, which are the ones we care about
    return rn_raw.decode().split("## v")[0].replace("\n## Unreleased\n", "", 1).replace("# Release Notes\n", "", 1)


def generate_rn(branch):

    subprocess.check_output(
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


def create_release_notes_sections(rn_raw, branch):
    # get anything in unreleased section in case there were updates since the last RC
    unreleased = clean_rn(rn_raw)
    unreleased = unreleased.split("###")[1:]
    try:
        unreleased_sections = dict(section.split("\n\n-") for section in unreleased)
        for key in unreleased_sections.keys():
            # add back in the "-" bullet point, and remove the extra newline
            unreleased_sections[key] = "-" + unreleased_sections[key][:-2]
    except ValueError:
        unreleased_sections = {}
    relevant_rns = []
    if unreleased_sections:
        relevant_rns.append(unreleased_sections)

    rns = rn_raw.decode().split("## v")
    for rn in rns:
        if rn.startswith("%s.0" % branch):
            # cut out the version section
            sections = rn.split("###")[1:]
            prelude_section = {}
            # if there is a prelude, we need to grab that separately since it has different syntax
            if sections[0].startswith(" Prelude\n\n"):
                prelude_section[" Prelude"] = sections[0].split("\n\n", 1)[1]
                sections = sections[1:]
            sections_dict = {**dict(section.split("\n\n", 1) for section in sections), **prelude_section}
            relevant_rns.append(sections_dict)
    # join all the sections from different relevant RCs together
    keys = set().union(*relevant_rns)
    rns_dict = {k: "".join(dic.get(k, "") for dic in relevant_rns) for k in keys}
    rn_sections_clean = {}
    for key in rns_dict.keys():
        rn_sections_clean[key.lstrip()] = rns_dict[key]
    return rn_sections_clean


def create_draft_release(
    branch,
    name,
    tag,
    rn,
    prerelease,
    dd_repo,
):
    base_branch = dd_repo.get_branch(branch=branch)
    print_release_notes = bool(os.getenv("PRINT"))
    if print_release_notes:
        print(
            """\nName:%s\nTag:%s\nprerelease:%s\ntarget_commitish:%s\nmessage:%s
              """
            % (name, tag, prerelease, base_branch, rn)
        )
    else:
        dd_repo.create_git_release(
            name=name, tag=tag, prerelease=prerelease, draft=True, target_commitish=base_branch, message=rn
        )
        print("Please review your release notes draft here: https://github.com/DataDog/dd-trace-py/releases")


if __name__ == "__main__":
    create_release_draft()
