import os
import sys
import subprocess

from .repository_url import normalize_repository_url


def __query_git(args):
    try:
        p = subprocess.Popen(["git"] + args, stdout=subprocess.PIPE)
    except EnvironmentError:
        print("Couldn't run git")
        return
    ver = p.communicate()[0]
    return ver.strip().decode("utf-8")


def __get_commit_sha():
    return __query_git(["rev-parse", "HEAD"])


def __get_repository_url():
    return __query_git(["config", "--get", "remote.origin.url"])


def get_source_code_link():
    return normalize_repository_url(__get_repository_url()) + "#" + __get_commit_sha()
