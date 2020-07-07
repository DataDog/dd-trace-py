import glob

import invoke

BUILDDIR = "build"
DISTDIR = "dist"


def _get_version(c):
    output = c.run("python setup.py --version", hide=True).stdout.strip()
    # take last line as potentially includes output from cython
    return output.split("\n")[-1]


def _get_branch(c):
    return c.run("git name-rev --name-only HEAD", hide=True).stdout.strip()


@invoke.task
def clean(c):
    patterns = [BUILDDIR, DISTDIR]
    for pattern in patterns:
        c.run("rm -rf {}".format(pattern))


@invoke.task(clean)
def build(c):
    c.run("scripts/build-dist", pty=True)


@invoke.task(build)
def release(c, upload=False):
    version = _get_version(c)
    branch = _get_branch(c)

    if branch.lower() != f"tags/v{version}":
        response = (
            input(
                f"WARNING: Expected current commit to be tagged as 'tags/v{version},"
                f"instead we are on '{branch}', proceed anyways [y|N]? "
            )
            .lower()
            .strip()
        )
        if response != "y":
            return

    response = (
        input(
            "WARNING: This task will build and release new wheels to https://pypi.org/project/ddtrace/"
            ", this action cannot be undone\n"
            f"         To proceed please type the version '{version}': "
        )
        .lower()
        .strip()
    )

    if response != version:
        return

    builds = glob.glob(f"{DISTDIR}/*.*")

    if len(builds) == 0:
        print(f"No builds found in {DISTDIR}")
        return

    c.run(f"twine check {DISTDIR}/*")

    upload_cmd = f"twine upload {DISTDIR}/*"

    if not upload:
        print(f"{upload_cmd} [skipping]")
        return

    c.run(upload_cmd)
