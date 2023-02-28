import os

#  Figure out the version of the library that you’re working on releasing grabbed with VERSION envar
version = os.getenv("VERSION")
if version is None:
    print("need to specify the version with envar e.g. VERSION=1.8.0")

# setup slack webclient
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
# https://api.slack.com/apps
# https://api.slack.com/authentication/basics
slack_token = os.getenv("SLACK_TOKEN")
client = WebClient(token=slack_token)


try:
    response = client.chat_postMessage(
        channel="zachg-test",
        text="Hello from your app! :tada:"
    )
except SlackApiError as e:
    # You will get a SlackApiError if "ok" is False
    assert e.response["error"]    # str like 'invalid_auth', 'channel_not_found'

# get release notes
import subprocess
# this works but commenting out for now for speed
rn_raw = subprocess.check_output("reno report --no-show-source | \
pandoc -f rst -t gfm --wrap=none", shell=True, cwd = os.pardir)
rn = rn_raw.decode().split("## v")[0].replace("\n## Unreleased\n", "", 1)
print(rn) 

# Create our git release https://pygithub.readthedocs.io/en/latest/github_objects/Repository.html?highlight=release#github.Repository.Repository.create_git_release
from github import Github
g = Github(os.getenv("GH_TOKEN"))
# dd_repo = g.get_repo(full_name_or_id="DataDog/dd-trace-py")
# main_branch = dd_repo.get_branch(branch="1.x")

dd_repo = g.get_repo(full_name_or_id="Kyle-Verhoog/dd-trace-py-test")
main_branch = dd_repo.get_branch(branch="master")

dd_repo.create_git_release(name=version, tag=version, prerelease=True, draft=True, target_commitish=main_branch, message=rn)
    
print("ok")
