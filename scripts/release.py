import json
import os
import re
import subprocess

from dotenv import load_dotenv
from github import Github
from github.GithubException import UnknownObjectException
from github.GithubException import GithubException


load_dotenv(dotenv_path=".env")

def create_release_draft():
    #  Figure out the version of the library that you’re working on releasing grabbed with VERSION envar
    base = os.getenv("BASE")
    gh_token = os.getenv("GH_TOKEN")
    rc = bool(os.getenv("RC"))
    branch_exists = None
    
    if base is None:
        raise ValueError("need to specify the base version with envar e.g. BASE=1.10.0")
    
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
    if branch_exists:
        # rc + 1
        if rc:
            # figure out the rc version we want
            search = "v%s.0\.?rc((\d+$))" % base
            tags = dd_repo.get_tags()
            rc_version = 1
            for tag in tags:
                try:
                    rc_num = re.findall(search, tag.name)[0][0]
                    rc_num = int(rc_num)
                except (IndexError, ValueError, TypeError):
                    continue
                if rc_num > rc_version:
                    rc_version = rc_num
            
            
            
            name = "%s.0rc%s" % (base, rc_version)
            import pdb; pdb.set_trace()
            # switch to base branch to get the latest
            rn_raw = subprocess.check_output(
                "git checkout {base} && \
            git pull origin {base} && \
            reno report --no-show-source | \
            pandoc -f rst -t gfm --wrap=none".format(base=base),
                shell=True,
                cwd=os.pardir,
            )
            rn = rn_raw.decode().split("## v")[0].replace("\n## Unreleased\n", "", 1).replace("# Release Notes\n", "", 1)
            
            base_branch = dd_repo.get_branch(branch=base)
            dd_repo.create_git_release(
                name=name, tag=name, prerelease=True, draft=True, target_commitish=base_branch, message=rn
            )
            exit()
        
        # patch release
        elif patch:
            pass
        # final draft
        else:
            pass
    # first rc
    elif rc:
        tag = base + ".0rc1"
        rn_raw = subprocess.check_output(
        "git checkout 1.x && \
        git pull && \
        reno report --no-show-source | \
        pandoc -f rst -t gfm --wrap=none",
            shell=True,
            cwd=os.pardir,
        )
        rn = rn_raw.decode().split("## v")[0].replace("\n## Unreleased\n", "", 1).replace("# Release Notes\n", "", 1)
        
        main_branch = dd_repo.get_branch(branch="1.x")
        # dd_repo.create_git_release(
        #     name=tag, tag=tag, prerelease=True, draft=True, target_commitish=main_branch, message=rn
        # )
    
    else:
        print("It looks like you didn't specify the release script params properly. Please take a look at how to use it and try again")
        
        

    # Figure out if we're doin a patch release or a minor release RC
    # By checking if the base branch already exists
    


if __name__ == "__main__":
    create_release_draft()
