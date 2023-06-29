import os
import subprocess


def test_mypackage_example(mypackage_example):
    git_sha = subprocess.check_output("git rev-parse HEAD", shell=True).decode("utf-8").strip()
    expected = "Project-URL: source_code_link, https://github.com/companydotcom/repo#{}".format(git_sha)
    subprocess.check_output("python setup.py bdist", shell=True)
    pkg_info = os.path.join(
        mypackage_example,
        "mypackage.egg-info",
        "PKG-INFO",
    )
    with open(pkg_info, "r") as f:
        links = [_.strip() for _ in f.readlines() if _.startswith("Project-URL: source_code_link, ")]

    assert len(links) == 1
    assert links[0] == expected
