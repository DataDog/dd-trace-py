import os
import re
from github import Github
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import subprocess
import json
import requests
from github.GithubException import UnknownObjectException

def release_it():

    #  Figure out the version of the library that you’re working on releasing grabbed with VERSION envar
    version = os.getenv("VERSION")
    last_version = os.getenv("LAST_VERSION")
    dd_api_key = os.getenv("DD_API_KEY")
    dd_app_key = os.getenv("DD_APP_KEY")
    gh_token = os.getenv("GH_TOKEN")
    slack_token = os.getenv("SLACK_TOKEN")
    

    if version is None:
        print("need to specify the version with envar e.g. VERSION=1.8.0")

    # setup slack webclient

    # https://api.slack.com/apps
    # https://api.slack.com/authentication/basics
    client = WebClient(token=slack_token)

    try:
        response = client.chat_postMessage(channel="zachg-test", text="Hello from your app! :tada:")
    except SlackApiError as e:
        # You will get a SlackApiError if "ok" is False
        assert e.response["error"]  # str like 'invalid_auth', 'channel_not_found'


    # Create our git release https://pygithub.readthedocs.io/en/latest/github_objects/Repository.html?highlight=release#github.Repository.Repository.create_git_release
    g = Github(gh_token)
    dd_repo = g.get_repo(full_name_or_id="DataDog/dd-trace-py")
    main_branch = dd_repo.get_branch(branch="1.x")
    # dd_repo = g.get_repo(full_name_or_id="Kyle-Verhoog/dd-trace-py-test")
    # main_branch = dd_repo.get_branch(branch="master")

    commit_id = main_branch.commit
    # dd_repo.create_git_release(name=version, tag=version, prerelease=True, draft=True, target_commitish=main_branch, message=rn)
    
    
    # compare 1.x to the latest release to get the commits, release notes, PRs and authors.    
    diff_raw = subprocess.check_output("git cherry -v {last_version} 1.x".format(last_version=last_version),
        shell=True,
        cwd=os.pardir,
    ).decode("utf8")
    commit_hashes = re.findall('[0-9a-f]{5,40}', diff_raw)
    commits = []
    # get the commit objects
    for commit_hash in commit_hashes:
        commits.append(dd_repo.get_commit(commit_hash))
        
        
    prs_details = []
    # from each commit:
    # 1. pull out author and turn into email
    # 2. get PR number and create link
    # 3. get release note text
    for commit in commits:
        files = commit.files
        for file in files:
            filename = file.filename
            if "releasenotes/notes/" in filename:
                # we need to make another api call to get ContentFile object so we can see what's in there
                rn_file_content = dd_repo.get_contents(filename).decoded_content.decode("utf8")
                # try to grab a good portion of the release note for us to later use to insert in our reno release notes
                # this is a bit hacky and will only attatch to one section if you have multiple sections in a release note 
                # (e.g. a prelude and a fix section):
                # for example: https://github.com/DataDog/dd-trace-py/blob/1.x/releasenotes/notes/asm-user-id-blocking-5048b1cef07c80fd.yaml
                rn_piece = re.search("    ((.|\n)*)", rn_file_content).group().strip()
                rn_piece = re.sub('\n    ', ' ', rn_piece)
                rn_piece = re.sub('``', '`', rn_piece)
                rn_piece = re.sub('  ', ' ', rn_piece)
                # rn_piece = re.findall("    ((.|\n)*)", rn_file_content)[0].strip()

                import pdb; pdb.set_trace()
                author = commit.author.name
                if author:
                    author = "@" + author.replace(" ", ".") + "@datadoghq.com"
                pr_num = re.findall('\(#(\d{4})\)', commit.commit.message)[0]
                url = "https://github.com/DataDog/dd-trace-py/pull/{pr_num}".format(pr_num=pr_num)
                prs_details.append({"author":author, "url":url, "rn_piece":rn_piece})
                
    print(prs_details)
        
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
        # piece = ".*" + pr_details["rn_piece"] + "/n- [ ]"
        rn_piece = pr_details["rn_piece"]
        import pdb; pdb.set_trace()
        # https://stackoverflow.com/questions/16510017/how-to-use-regular-expressions-do-reverse-search
        # g,s,e = [(m.group(),m.start(),m.end()) for m in re.finditer(piece, rn)][-1]
        i = rn.rfind(rn_piece)
        if i != -1:
            e = i + len(rn_piece)
        # check to make sure there was a match
            rn = rn[:e] + "\nPR:{pr}\nTester: {author}".format(pr=pr_details["url"],author=pr_details["author"]) + rn[e:]
        
        
        
        
    print(rn)
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
        "#  Release notes to test\n- %s\n<Tester> \n<PR>\n\n\n## Release Notes that will not be tested\n- <any release notes for PRs that don't need manual testing>\n\n\n"
        % (rn)
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

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "DD-API-KEY": dd_api_key,
        "DD-APPLICATION-KEY": dd_app_key,
    }
    # create new release notebook
    resp_create = requests.post("https://api.datadoghq.com/api/v1/notebooks", data=notebook_json, headers=headers)

    print("ok")


if __name__ == "__main__":
    release_it()
