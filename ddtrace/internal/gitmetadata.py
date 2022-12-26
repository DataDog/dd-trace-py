import os
import inspect
import subprocess


def __find_package():
    frm = inspect.stack()[-1]
    module = inspect.getmodule(frm[0])
    if module is None:
        return None
    path = module.__file__
    package = ""
    while len(path) > 1:
        path, end = os.path.split(path)
        if end in ("site-packages", "dist-packages"):
            break
        package = end
    return package.split("-")[0]


# get_tags extract git metadata from python package's medatada field Project-URL:
# e.g: Project-URL: source_code_link, https://github.com/user/repo#gitcommitsha&someoptions
def get_tags(package=None):
    if package is None:
        package = __find_package()
    if package is None:
        return {}
    try:
        try:
            import importlib.metadata as importlib_metadata
        except ImportError:
            import importlib_metadata  # type: ignore[no-redef]

        source_code_link = ""
        for val in importlib_metadata.metadata(package).get_all("Project-URL"):
            capt_val = val.split(", ")
            if capt_val[0] == "source_code_link":
                source_code_link = capt_val[1].strip()
                break

        if source_code_link != "" and "#" in source_code_link:
            repository_url, commit_sha = source_code_link.split("#")
            commit_sha = commit_sha.split("&")[0]
            return {"git.repository_url": repository_url, "git.commit.sha": commit_sha}
        return {}
    except importlib.metadata.PackageNotFoundError:
        return {}
