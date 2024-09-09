import re

from ddtrace.sourcecode._utils import get_source_code_link


def test_get_source_code_link():
    source_code_link = get_source_code_link()
    reporsitory_url, commit_hash = source_code_link.split("#")

    # can be github.com or gitlab.ddbuild.io
    assert reporsitory_url.startswith("https://git")
    assert reporsitory_url.endswith("/dd-trace-py")
    assert re.match(r"\b[a-f0-9]{5,40}\b", commit_hash) is not None
