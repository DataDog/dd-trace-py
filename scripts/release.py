from collections import namedtuple
import json
import os
import re
import subprocess

from datadog_api_client import ApiClient
from datadog_api_client import Configuration
from datadog_api_client.v1.api.notebooks_api import NotebooksApi
from github import Github
import requests


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
    BASE - The base branch you are building your release candidate, patch, or minor release off of.
        If this is a rc1, then just specify the branch you'll create after the release is published. e.g. BASE=2.9

Optional:
    RC - Whether or not this is a release candidate. e.g. RC=1 or RC=0
    PATCH - Whether or not this a patch release. e.g. PATCH=1 or PATCH=0
    PRINT - Whether or not the release notes should be printed to CLI or be used to create a Github release. Default is 0 e.g. PRINT=1 or PRINT=0
    NOTEBOOK - Whether or not to create a notebook in staging. Note this only works for RC1s since those are usually what we create notebooks for.
    Default is 1 for RC1s, 0 for everything else e.g. NOTEBOOK=0 or NOTEBOOK=1
Examples:
Generate release notes and staging testing notebook for next release candidate version of 2.11: `BASE=2.11 RC=1 python release.py`

Generate release notes for next patch version of 2.13: `BASE=2.13 PATCH=1 python release.py`

Generate release notes for the 2.15 release: `BASE=2.15 python release.py`
"""  # noqa

MAX_GH_RELEASE_NOTES_LENGTH = 125000
ReleaseParameters = namedtuple("ReleaseParameters", ["branch", "name", "tag", "dd_repo", "rn", "prerelease"])


def _ensure_current_checkout():
    subprocess.run("git fetch", shell=True, cwd=os.pardir)


def _decide_next_release_number(base: str, candidate: bool = False) -> int:
    """Return the next number to use as a patch or release candidate version, based on existing tags on the remote"""
    search = r"v%s.0\.?rc((\d+$))" % base if candidate else r"v%s.((\d+))" % base
    tags = dd_repo.get_tags()
    latest_version = 0
    for tag in tags:
        try:
            other_num = int(re.findall(search, tag.name)[0][0])
        except (IndexError, ValueError, TypeError):
            continue
        if other_num > latest_version:
            latest_version = other_num
    return latest_version + 1


def _get_rc_parameters(dd_repo, base: str, rc, patch, latest_branch) -> ReleaseParameters:
    """Build a ReleaseParameters object representing the in-progress release candidate"""
    new_rc_version = _decide_next_release_number(base, candidate=True)
    release_branch = latest_branch if new_rc_version == 1 else base
    rn = clean_release_notes(generate_release_notes(release_branch))
    return ReleaseParameters(release_branch, "%s.0rc%s" % (base, str(new_rc_version)), "v%s" % name, dd_repo, rn, True)


def _get_patch_parameters(dd_repo, base: str, rc, patch, latest_branch) -> ReleaseParameters:
    """Build a ReleaseParameters object representing the in-progress patch release"""
    name = "%s.%s" % (base, str(_decide_next_release_number(base)))
    release_notes = clean_release_notes(generate_release_notes(base))
    return ReleaseParameters(base, name, "v%s" % name, dd_repo, release_notes, False)


def _get_minor_parameters(dd_repo, base: str, rc, patch, latest_branch) -> ReleaseParameters:
    """Build a ReleaseParameters object representing the in-progress minor release"""
    name = "%s.0" % base

    rn_raw = generate_release_notes(base)
    rn_sections_clean = create_release_notes_sections(rn_raw, base)
    release_notes = ""
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
            release_notes += "### %s\n\n%s" % (key, rn_sections_clean[key])
        except KeyError:
            continue
    return ReleaseParameters(base, name, "v%s" % name, dd_repo, release_notes, False)


def create_release_draft(dd_repo, base, rc, patch, latest_branch):
    _ensure_current_checkout()
    args = (dd_repo, base, rc, patch, latest_branch)
    if rc:
        parameters = _get_rc_parameters(*args)
    elif patch:
        parameters = _get_patch_parameters(*args)
    else:
        parameters = _get_minor_parameters(*args)

    create_draft_release_github(parameters)

    return parameters.name, parameters.rn


def clean_release_notes(rn_raw: str) -> str:
    """removes everything from the given string except for release notes that haven't been released yet"""
    return rn_raw.decode().split("## v")[0].replace("\n## Unreleased\n", "", 1).replace("# Release Notes\n", "", 1)


def generate_release_notes(branch: str) -> str:
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
    unreleased = clean_release_notes(rn_raw)
    unreleased = break_into_release_sections(unreleased)
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
    # grab the sections from the RC(s)
    for rn in rns:
        if rn.startswith("%s.0" % branch):
            # cut out the version section
            sections = break_into_release_sections(rn)
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


def break_into_release_sections(rn):
    return [ele for ele in re.split(r"[^#](#)\1{2}[^#]", rn)[1:] if ele != "#"]


def create_draft_release_github(release_parameters: ReleaseParameters):
    base_branch = dd_repo.get_branch(branch=release_parameters.branch)
    print_release_notes = bool(os.getenv("PRINT"))
    if print_release_notes:
        print(
            """RELEASE NOTES INFO:\nName:%s\nTag:%s\nprerelease:%s\ntarget_commitish:%s\nmessage:%s
              """
            % (
                release_parameters.name,
                release_parameters.tag,
                release_parameters.prerelease,
                base_branch,
                release_parameters.rn,
            )
        )
    else:
        dd_repo.create_git_release(
            name=release_parameters.name,
            tag=release_parameters.tag,
            prerelease=release_parameters.prerelease,
            draft=True,
            target_commitish=base_branch,
            message=release_parameters.rn[:MAX_GH_RELEASE_NOTES_LENGTH],
        )
        print("\nPlease review your release notes draft here: https://github.com/DataDog/dd-trace-py/releases")

    return release_parameters.name, release_parameters.rn


def get_ddtrace_repo():
    gh_token = os.getenv("GH_TOKEN")
    if not gh_token:
        raise ValueError(
            "We need a Github token to generate the release notes. Please follow the instructions in the script."
        )
    return Github(gh_token).get_repo(full_name_or_id="DataDog/dd-trace-py")


def create_notebook(dd_repo, name, rn, base, latest_branch):
    dd_api_key = os.getenv("DD_API_KEY_STAGING")
    dd_app_key = os.getenv("DD_APP_KEY_STAGING")
    if not dd_api_key or not dd_app_key:
        raise ValueError(
            "We need DD_API_KEY_STAGING and DD_APP_KEY_STAGING values. Please follow the instructions in the script."
        )
    if int(name[-1]) == 1:
        last_version = base[:-1] + str(int(base[-1]) - 1)
    else:
        print(
            "Since this is not the RC1 for this release."
            "Please add the release notes for this release to the notebook."
        )
        return
    commit_hashes = (
        subprocess.check_output(
            'git log {last_version}..{latest_branch} --oneline | cut -d " " -f 1'.format(
                last_version=last_version, latest_branch=latest_branch
            ),
            shell=True,
            cwd=os.pardir,
        )
        .decode("utf8")
        .strip("\n")
        .split("\n")
    )

    commits = []
    # get the commit objects
    for commit_hash in commit_hashes:
        try:
            commits.append(dd_repo.get_commit(commit_hash))
        except Exception:
            print("Couldn't get commit hash %s for notebook, please add this manually" % commit_hash)

    # get list of authors for when we make the slack announcement
    author_slack_handles = []
    prs_details = []
    # from each commit:
    # 1. get release note text
    # 2. pull out author and turn into email
    # 3. get PR number and create link
    for commit in commits:
        files = commit.files
        for file in files:
            filename = file.filename
            if filename.startswith("releasenotes/notes/"):
                try:
                    # we need to make another api call to get ContentFile object so we can see what's in there
                    rn_file_content = dd_repo.get_contents(filename).decoded_content.decode("utf8")
                    # try to grab a good portion of the release note for us to use to insert in our reno release notes
                    # this is a bit hacky, will only attach to one section if you have multiple sections
                    # in a release note
                    # (e.g. a features and a fix section):
                    # for example: https://github.com/DataDog/dd-trace-py/blob/1.x/releasenotes/notes/asm-user-id-blocking-5048b1cef07c80fd.yaml # noqa
                except Exception:
                    print(
                        """File contents were not obtained for {file} in commit {commit}."""
                        """It's likely this file was deleted in a PR""".format(file=file, commit=commit)
                    )
                    continue
                try:
                    rn_piece = re.findall(
                        r"  - \|\n    ((.|\n)*)\n(((issues|features|upgrade|deprecations|fixes|other):\n)|.*)",
                        rn_file_content,
                    )[0][0].strip()
                    rn_piece = re.sub("\n    ", " ", rn_piece)
                    # if you use the pattern  \s\n\s\s\s\s (which you shouldn't)
                    # then the sub above will leave a double space
                    rn_piece = re.sub("  ", " ", rn_piece)
                    rn_piece = re.sub("``", "`", rn_piece)
                except Exception:
                    continue
                author = commit.author.name
                if author:
                    author = author.split(" ")
                    author_slack = "@" + author[0] + "." + author[-1]
                    author_slack_handles.append(author_slack)
                    author_dd = author_slack + "@datadoghq.com"
                pr_num = re.findall(r"\(#(\d{4})\)", commit.commit.message)[0]
                url = "https://github.com/DataDog/dd-trace-py/pull/{pr_num}".format(pr_num=pr_num)
                prs_details.append({"author_dd": author_dd, "url": url, "rn_piece": rn_piece})

    # edit release notes to be put inside notebook
    rn = rn.replace("\n-", "\n- [ ]")
    for pr_details in prs_details:
        rn_piece = pr_details["rn_piece"]
        i = rn.rfind(rn_piece)
        if i != -1:
            e = i + len(rn_piece)
            # check to make sure there was a match
            rn = (
                rn[:e]
                + "\nPR:{pr}\nTester: {author_dd}".format(pr=pr_details["url"], author_dd=pr_details["author_dd"])
                + rn[e:]
            )

    # create the review notebook and add the release notes formatted for testing
    # get notebook template
    template_notebook = subprocess.check_output(
        'curl -X GET "https://api.datadoghq.com/api/v1/notebooks/4888117" \
    -H "Accept: application/json" \
    -H "DD-API-KEY: {dd_api_key}" \
    -H "DD-APPLICATION-KEY: {dd_app_key}"'.format(
            dd_api_key=dd_api_key, dd_app_key=dd_app_key
        ),
        shell=True,
        cwd=os.pardir,
    )
    data = template_notebook.decode("utf8")
    data = json.loads(data)
    # change the text inside of our template to include release notes
    data["data"]["attributes"]["cells"][1]["attributes"]["definition"]["text"] = (
        "#  Release notes to test\n-[ ] Relenv is checked: https://ddstaging.datadoghq.com/dashboard/h8c-888-v2e/python-reliability-env-dashboard \n\n%s\n<Tester> \n<PR>\n\n\n## Release Notes that will not be tested\n- <any release notes for PRs that don't need manual testing>\n\n\n"  # noqa
        % (rn)
    )
    # grab the latest commit id on the latest branch to mark the rc notebook with
    main_branch = dd_repo.get_branch(branch=latest_branch)
    commit_id = main_branch.commit

    # pull the cells out to be transferred into a new notebook
    cells = data["data"]["attributes"]["cells"]
    nb_name = "ddtrace-py testing %s commit %s" % (name, commit_id.sha)
    interpolated_notebook = {
        "data": {
            "attributes": {
                "cells": cells,
                "name": nb_name,
                "time": {"live_span": "1d"},
            },
            "type": "notebooks",
        }
    }

    notebook_json = json.dumps(interpolated_notebook, indent=4, sort_keys=True)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "DD-API-KEY": dd_api_key,
        "DD-APPLICATION-KEY": dd_app_key,
    }
    # create new release notebook
    requests.post("https://api.datadoghq.com/api/v1/notebooks", data=notebook_json, headers=headers)

    configuration = Configuration()
    configuration.api_key["apiKeyAuth"] = dd_api_key
    configuration.api_key["appKeyAuth"] = dd_app_key
    with ApiClient(configuration) as api_client:
        api_instance = NotebooksApi(api_client)
        response = api_instance.list_notebooks(query=nb_name)

    nb_id = response._data_store["data"][0]["id"]
    nb_url = "https://ddstaging.datadoghq.com/notebook/%s" % (nb_id)

    print("\nNotebook created at %s\n" % nb_url)

    # eliminate duplicates of handles for the slack messages
    author_slack_handles = " ".join(set(author_slack_handles))

    print("Message to post in #apm-python-release once deployed to staging:\n")
    print(
        """It's time to test the {version} release candidate! The owners of pull requests with release notes in {version} are {author_slack_handles}.
Everyone mentioned here: before the end of the day tomorrow, please ensure that you've filled in the testing strategy in the release notebook {nb_url} on all release notes you're the owner of, according to the expectations here: https://datadoghq.atlassian.net/wiki/spaces/APMPY/pages/2868085694/Staging+testing+expectations+for+dd-trace-py+contributors
You can start doing your tests immediately, using {version}.

Check the release notebook {nb_url} for asynchronous updates on the release process. If you have questions or need help with anything, let me know! I'm here to help. Thanks all for your dedication to maintaining a rock-solid library.""".format(  # noqa
            version=name, author_slack_handles=author_slack_handles, nb_url=nb_url
        )
    )


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

    base = os.getenv("BASE")
    rc = bool(os.getenv("RC"))
    patch = bool(os.getenv("PATCH"))
    latest_branch = base[0] + ".x"

    if base is None:
        raise ValueError("Need to specify the base version with envar e.g. BASE=2.10")
    if ".x" in base:
        raise ValueError("Base branch must be a fully qualified semantic version.")

    dd_repo = get_ddtrace_repo()
    name, rn = create_release_draft(dd_repo, base, rc, patch, latest_branch)

    if rc:
        if os.getenv("NOTEBOOK", 1):
            print("Creating Notebook")
            create_notebook(dd_repo, name, rn, base, latest_branch)
        else:
            print(
                (
                    "Currently the release script only supports making notebooks for RC1s."
                    "No notebook will be created at this time."
                )
            )

    # switch back to original git branch
    subprocess.check_output(
        "git checkout {start_branch}".format(start_branch=start_branch),
        shell=True,
        cwd=os.pardir,
    )
    print(
        (
            "\nYou've been switch back to your original branch, if you had uncommitted changes before"
            "running this command, run `git stash pop` to get them back."
        )
    )
