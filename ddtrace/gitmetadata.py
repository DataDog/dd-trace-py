import os
import inspect
import subprocess
import pkg_resources


def __query_git(args):
    try:
        p = subprocess.Popen(["git"] + args, stdout=subprocess.PIPE)
    except EnvironmentError:
        print("Couldn't run git")
        return
    ver = p.communicate()[0]
    return ver.strip().decode("utf-8")


def get_commit_sha():
    return __query_git(["rev-parse", "HEAD"])


def get_repository_url():
    return __query_git(["config", "--get", "remote.origin.url"])


def add_project_urls(setup):
    source_code_link = get_repository_url() + "#" + get_commit_sha()

    def patch(**attrs):
        if "project_urls" not in attrs:
            attrs["project_urls"] = {}
        attrs["project_urls"]["source_code_link"] = source_code_link
        return setup(**attrs)

    return patch


def __find_package():
    frm = inspect.stack()[-1]
    path = inspect.getmodule(frm[0]).__file__
    package = ""
    while len(path) > 1:
        path, end = os.path.split(path)
        if end == "site-packages":
            break
        package = end
    return package.split("-")[0]


def get_tags(package=None):
    if package == None:
        package = __find_package()
    try:
        pkg = pkg_resources.require(package)[0]
        metadata_lines = []
        if pkg.has_metadata("METADATA"):
            metadata_lines = pkg.get_metadata_lines("METADATA")
        if pkg.has_metadata("PKG-INFO"):
            metadata_lines = pkg.get_metadata_lines("PKG-INFO")

        source_code_link = ""
        for metadata in metadata_lines:
            name_val = metadata.split(": ")
            if name_val[0] == "Project-URL":
                capt_val = name_val[1].split(", ")
                if capt_val[0] == "source_code_link":
                    source_code_link = capt_val[1].strip()
                    break

        if source_code_link != "" and "#" in source_code_link:
            repository_url, commit_sha = source_code_link.split("#")
            return {"git.repository_url": repository_url, "git.commit.sha": commit_sha}
        return {}
    except pkg_resources.DistributionNotFound:
        return {}


import distutils.core

distutils.core.setup = add_project_urls(distutils.core.setup)

import setuptools

setuptools.setup = add_project_urls(setuptools.setup)
