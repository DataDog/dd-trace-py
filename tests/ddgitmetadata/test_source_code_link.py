import re

from ddgitmetadata import get_source_code_link


def test_get_source_code_link():
    source_code_link = get_source_code_link()
    reporsitory_url, commit_hash = source_code_link.split('#')

    assert reporsitory_url == "https://github.com/sashacmc/dd-git-metadata-poc"
    assert re.match(r'\b[a-f0-9]{5,40}\b', commit_hash) is not None
