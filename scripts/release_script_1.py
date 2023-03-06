import json
import os
import re
import subprocess

from dotenv import load_dotenv
from github import Github
from github.GithubException import UnknownObjectException
import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


load_dotenv(dotenv_path=".env")


def release_it():

    #  Figure out the version of the library that you’re working on releasing grabbed with VERSION envar
    version = os.getenv("VERSION")
    last_version = os.getenv("LAST_VERSION")
    dd_api_key = os.getenv("DD_API_KEY")
    dd_app_key = os.getenv("DD_APP_KEY")
    gh_token = os.getenv("GH_TOKEN")
    slack_token = os.getenv("SLACK_TOKEN")

    if version is None:
        raise ValueError("need to specify the version with envar e.g. VERSION=1.10.0rc1")
    if last_version is None:
        raise ValueError("need to specify the last_version branch with envar e.g. LAST_VERSION=1.9")

    # Create our git release https://pygithub.readthedocs.io/en/latest/github_objects/Repository.html?highlight=release#github.Repository.Repository.create_git_release
    g = Github(gh_token)
    dd_repo = g.get_repo(full_name_or_id="DataDog/dd-trace-py")
    main_branch = dd_repo.get_branch(branch="1.x")
    # dd_repo = g.get_repo(full_name_or_id="Kyle-Verhoog/dd-trace-py-test")
    # main_branch = dd_repo.get_branch(branch="master")

    commit_id = main_branch.commit
    # dd_repo.create_git_release(name=version, tag=version, prerelease=True, draft=True, target_commitish=main_branch, message=rn)

    # compare 1.x to the latest release to get the commits, release notes, PRs and authors.
    diff_raw = subprocess.check_output(
        "git cherry -v {last_version} 1.x".format(last_version=last_version),
        shell=True,
        cwd=os.pardir,
    ).decode("utf8")
    commit_hashes = re.findall("[0-9a-f]{5,40}", diff_raw)
    commits = []
    # get the commit objects
    for commit_hash in commit_hashes:
        commits.append(dd_repo.get_commit(commit_hash))

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
            if "releasenotes/notes/" in filename:
                # we need to make another api call to get ContentFile object so we can see what's in there
                rn_file_content = dd_repo.get_contents(filename).decoded_content.decode("utf8")
                # try to grab a good portion of the release note for us to later use to insert in our reno release notes
                # this is a bit hacky and will only attatch to one section if you have multiple sections in a release note
                # (e.g. a features and a fix section):
                # for example: https://github.com/DataDog/dd-trace-py/blob/1.x/releasenotes/notes/asm-user-id-blocking-5048b1cef07c80fd.yaml
                try:
                    rn_piece = re.findall(
                        "  - \|\n    ((.|\n)*)\n(((issues|features|upgrade|deprecations|fixes|other):\n)|.*)",
                        rn_file_content,
                    )[0][0].strip()
                    rn_piece = re.sub("\n    ", " ", rn_piece)
                    # if you use the pattern  \s\n\s\s\s\s (which you shouldn't) then the sub above will leave a double space
                    rn_piece = re.sub("  ", " ", rn_piece)
                    rn_piece = re.sub("``", "`", rn_piece)
                except Exception:
                    continue
                author = commit.author.name
                if author:
                    # only grab first and last name of author
                    author = author.split(" ")
                    author_slack = "@" + author[0] + "." + author[-1]
                    author_slack_handles.append(author_slack)
                    author_dd = author_slack + "@datadoghq.com"
                pr_num = re.findall("\(#(\d{4})\)", commit.commit.message)[0]
                url = "https://github.com/DataDog/dd-trace-py/pull/{pr_num}".format(pr_num=pr_num)
                prs_details.append({"author_dd": author_dd, "url": url, "rn_piece": rn_piece})

    # get release notes
    rn_raw = subprocess.check_output(
        "reno report --no-show-source | \
    pandoc -f rst -t gfm --wrap=none",
        shell=True,
        cwd=os.pardir,
    )
    rn = rn_raw.decode().split("## v")[0].replace("\n## Unreleased\n", "", 1).replace("# Release Notes\n", "", 1)

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
        "#  Release notes to test\n%s\n<Tester> \n<PR>\n\n\n## Release Notes that will not be tested\n- <any release notes for PRs that don't need manual testing>\n\n\n"
        % (rn)
    )
    # grab the cells out to be transferred into a new notebook
    cells = data["data"]["attributes"]["cells"]
    nb_name = "ddtrace-py testing %s commit %s" % (version, commit_id.sha)
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
    resp_create = requests.post("https://api.datadoghq.com/api/v1/notebooks", data=notebook_json, headers=headers)

    # get notebook url for slack announcement
    from datadog_api_client import ApiClient
    from datadog_api_client import Configuration
    from datadog_api_client.v1.api.notebooks_api import NotebooksApi

    configuration = Configuration()
    with ApiClient(configuration) as api_client:
        api_instance = NotebooksApi(api_client)
        response = api_instance.list_notebooks(query=nb_name)

    id = response._data_store["data"][0]["id"]
    nb_url = "https://ddstaging.datadoghq.com/notebook/%s" % (id)
    
    print("Notebook created at %s" % url)
    
    author_slack_handles = " ".join(author_slack_handles)
    
    print("Message to post in #apm-python-release once deployed to staging:\n")
    print(
        """It's time to test the {version} release candidate! The owners of pull requests with release notes in version are {author_slack_handles}. 
Everyone mentioned here: before the end of the day tomorrow, please ensure that you've filled in the testing strategy in the release notebook {nb_url} on all release notes you're the owner of, according to the expectations here. Please also note when you expect the testing itself to be completed (ASAP preferred).
You can start doing your tests immediately, using {version}. If you have questions or need help with anything, let me know! I'm here to help. Thanks all for your dedication to maintaining a rock-solid library.

Check the release notebook {nb_url} for asynchronous updates on the release process. If you have questions or need help with anything, let me know! I'm here to help. Thanks all for your dedication to maintaining a rock-solid library.""".format(
            version=version, author_slack_handles=author_slack_handles, nb_url=nb_url
        )
    )

    # setup slack webclient
    # https://api.slack.com/apps
    # https://api.slack.com/authentication/basics
    client = WebClient(token=slack_token)

    # formulate slack announcement

    try:
        response = client.chat_postMessage(channel="zachg-test", text="Hello from your app! :tada:")
    except SlackApiError as e:
        # You will get a SlackApiError if "ok" is False
        assert e.response["error"]  # str like 'invalid_auth', 'channel_not_found'


if __name__ == "__main__":
    release_it()
