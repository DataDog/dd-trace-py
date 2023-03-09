from .source_code_link import get_source_code_link
from .repository_url import normalize_repository_url


def __add_project_urls(setup):
    source_code_link = get_source_code_link()

    def patch(**attrs):
        if "project_urls" not in attrs:
            attrs["project_urls"] = {}
        attrs["project_urls"]["source_code_link"] = source_code_link
        return setup(**attrs)

    return patch


def patch_setup():
    import distutils.core
    import setuptools

    distutils.core.setup = __add_project_urls(distutils.core.setup)

    setuptools.setup = __add_project_urls(setuptools.setup)


patch_setup()
