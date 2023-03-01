import os


def release_it():

    #  Figure out the version of the library that you’re working on releasing grabbed with VERSION envar
    version = os.getenv("VERSION")
    dd_api_key = os.getenv("DD_API_KEY")
    dd_app_key = os.getenv("DD_APP_KEY")
    gh_token = os.getenv("GH_TOKEN")
    slack_token = os.getenv("SLACK_TOKEN")

    if version is None:
        print("need to specify the version with envar e.g. VERSION=1.8.0")

    # setup slack webclient
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    # https://api.slack.com/apps
    # https://api.slack.com/authentication/basics
    client = WebClient(token=slack_token)

    try:
        response = client.chat_postMessage(channel="zachg-test", text="Hello from your app! :tada:")
    except SlackApiError as e:
        # You will get a SlackApiError if "ok" is False
        assert e.response["error"]  # str like 'invalid_auth', 'channel_not_found'

    # get release notes
    import subprocess

    rn_raw = subprocess.check_output(
        "reno report --no-show-source | \
    pandoc -f rst -t gfm --wrap=none",
        shell=True,
        cwd=os.pardir,
    )
    rn = rn_raw.decode().split("## v")[0].replace("\n## Unreleased\n", "", 1).replace("# Release Notes\n", "", 1)
    print(rn)

    # Create our git release https://pygithub.readthedocs.io/en/latest/github_objects/Repository.html?highlight=release#github.Repository.Repository.create_git_release
    from github import Github

    g = Github(gh_token)
    # dd_repo = g.get_repo(full_name_or_id="DataDog/dd-trace-py")
    # main_branch = dd_repo.get_branch(branch="1.x")
    dd_repo = g.get_repo(full_name_or_id="Kyle-Verhoog/dd-trace-py-test")
    main_branch = dd_repo.get_branch(branch="master")

    commit_id = main_branch.commit
    # dd_repo.create_git_release(name=version, tag=version, prerelease=True, draft=True, target_commitish=main_branch, message=rn)
    
    # compare 1.x to the latest release to get the commits, release notes, PRs and authors.
    #     diff = dd_repo.compare("e6029d50785bd09f7dfa1aa78c976ccd42115e57", "2a87fa2160e23d6444947bd2a17d6881848118a2")

    diff = dd_repo.compare("1.9", "1.x")
    import pdb; pdb.set_trace()

    # edit release notes to be put inside notebook
    rn_notebook = rn.replace("-", "- [ ]")

    # create the review notebook and add the release notes formatted for testing
    import json

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
    # change the value inside of our template to include release notes
    data["data"]["attributes"]["cells"][1]["attributes"]["definition"]["text"] = (
        "#  Release notes to test\n- %s\n<Tester> \n<PR>\n\n\n## Release Notes that will not be tested\n- <any release notes for PRs that don't need manual testing>\n\n\n"
        % (rn_notebook)
    )
    # grab the cells out to be transferred into a new notebook
    cells = data["data"]["attributes"]["cells"]

    interpolated_notebook = {
        "data": {
            "attributes": {
                "cells": cells,
                "name": "ddtrace-py testing %s commit %s" % (version, commit_id.sha),
                "time": {"live_span": "1d"},
            },
            "type": "notebooks",
        }
    }

    notebook_json = json.dumps(interpolated_notebook, indent=4, sort_keys=True)

    import requests

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "DD-API-KEY": dd_api_key,
        "DD-APPLICATION-KEY": dd_app_key,
    }
    # create new release notebook
    # resp_create = requests.post("https://api.datadoghq.com/api/v1/notebooks", data=notebook_json, headers=headers)

    print("ok")


if __name__ == "__main__":
    release_it()
