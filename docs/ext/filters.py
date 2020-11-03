import re

from enchant.tokenize import Filter


# from setuptools-scm
# https://github.com/pypa/setuptools_scm/blob/69b88a20c5cd4632ef0c97b3ddd2bd0d3f8f7df8/src/setuptools_scm/config.py#L9
VERSION_TAG_REGEX_STRING = r"^(?:[\w-]+-)?(?P<version>[vV]?\d+(?:\.\d+){0,2}[^\+]*)(?:\+.*)?$"
VERSION_TAG_REGEX = re.compile(VERSION_TAG_REGEX_STRING)


class VersionTagFilter(Filter):
    """If a word matches a version tag used for the repository"""

    def _skip(self, word):
        return VERSION_TAG_REGEX.match(word)
