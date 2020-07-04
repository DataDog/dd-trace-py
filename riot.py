import argparse
import itertools
import logging
import os
import re
import shutil
import subprocess
import sys


logger = logging.getLogger(__name__)


class Dict(dict):
    def __init__(self, *args, **kwargs):
        super(Dict, self).__init__(*args, **kwargs)
        self.__dict__ = self


def rmchars(chars, s):
    for c in chars:
        s = s.replace(c, "")
    return s


global_deps = [
    "mock",
    "opentracing",
    "pytest<4",
    "pytest-benchmark",
]

global_env = Dict(PYTEST_ADDOPTS="--color=yes",)


all_groups = [
    Dict(
        name="redis",
        pys=[2.7, 3.7,],
        # pys=[2.7, 3.5, 3.6, 3.7, 3.8,],
        # pys=[3.7,],
        # deps=[("redis", ">=2.10,<2.11")],
        deps=[("redis", ">=2.10,<2.11 >=3.0,<3.1 >=3.2,<3.3 >=3.4,<3.5 >=3.5,<3.6")],
        command="pytest tests/contrib/redis/",
        env=dict(),
    ),
]


def venv_command(venv, cmd):
    return "source %s/bin/activate && %s" % (venv, cmd)


def run_in_venv(venv, cmd, out=False, env=None):
    env = env or {}

    env_str = " ".join("%s=%s" % (k, v) for k, v in env.items())
    cmd = venv_command(venv, cmd)

    logger.info("Executing command '%s' with environment '%s'", cmd, env_str)
    r = subprocess.run(cmd, stdout=subprocess.PIPE, shell=True, env=env)
    if out:
        print(r.stdout.decode("ascii"))
    return r


def group_iter(group):
    """Iterator over all configurations of a group.

    Yields the dependencies unique to the configuration of a group.
    """
    all_deps = []
    for lib, deps in group.deps:
        all_deps.append([(lib, v) for v in deps.split(" ")])

    all_deps = itertools.product(*all_deps)
    for deps in all_deps:
        yield deps


def groups_iter(groups, py=None, pattern=".*"):
    """Iterator over an interable of groups.

    :param py: An optional python version to match against.
    :param pattern: An optional pattern to match groups against.
    """
    pattern = re.compile(pattern)
    for g in groups:
        if not pattern.match(g.name):
            log.debug("Skipping group '%s' due to mismatch.", g.name)
            continue
        for gpy in g.pys:
            if py and gpy != py:
                continue
            for deps in group_iter(g):
                yield g, gpy, deps


def run_groups(
    groups,
    matcher=".*",
    skip_global_deps_install=False,
    skip_base_install=False,
    out=sys.stdout,
    encoding="utf-8",
    pass_env=False,
):
    """Runs the command for each group in `groups` in a unique virtual
    environment.
    """
    pattern = re.compile(matcher)
    results = []

    for group in groups:
        if not pattern.match(group.name):
            log.debug("Skipping group '%s' due to mismatch.", group.name)
            continue

        for py in group.pys:
            venv_base = ".venv_py%s" % str(py).replace(".", "")
            py_ex = "python%s" % py
            py_ex = shutil.which(py_ex)

            if not py_ex:
                print("%s interpreter not found" % py_ex)
                sys.exit(1)
            else:
                logger.info("Found Python interpreter '%s'.", py_ex)

            if not os.path.isdir(venv_base):
                # Create the base venv.
                logger.info("Creating virtualenv '%s' with Python '%s'.", venv_base, py_ex)
                r = subprocess.run(
                    ["virtualenv", "--python=%s" % py_ex, venv_base], stdout=subprocess.PIPE, encoding=encoding
                )
                if r.returncode:
                    print(r.stdout, file=out)
                    sys.exit(1)

                # Override skipping the base install since we just had to
                # create a new base env.
                if skip_base_install:
                    logger.warning(
                        "Overriding option to skip base install since a new base virtual env '%s' was created.",
                        venv_base,
                    )
                skip_base_install = False

            if not skip_base_install:
                # Install the global dependencies into the base venv.
                global_deps_str = " ".join(["'%s'" % dep for dep in global_deps])
                logger.info("Installing global dependencies into virtualenv '%s'.", global_deps_str)
                r = run_in_venv(venv_base, "pip install %s" % global_deps_str)

                # Install the dev package into the base venv.
                logger.info("Installing dev package.")
                r = run_in_venv(venv_base, "pip install -e .")
            else:
                logger.info("Skipping base install.")

            # Loop over each dependency configuration within the group.
            for deps in group_iter(group):
                # Strip special characters for the directory name.
                venv = "_".join(["%s%s" % (lib, rmchars("<=>.,", vers)) for lib, vers in deps])
                venv = "%s_%s" % (venv_base, venv)

                # Copy the base venv to use for this group.
                logger.info("Copying base virtualenv '%s' into group virtual env '%s'.", venv_base, venv)
                r = subprocess.run(["cp", "-r", venv_base, venv], stdout=subprocess.PIPE)

                # Install the group dependencies.
                dep_str = " ".join(["'%s%s'" % (lib, version) for lib, version in deps])
                logger.info("Installing group dependencies %s.", dep_str)
                r = run_in_venv(venv, "pip install %s" % dep_str)
                if r.returncode != 0:
                    print(r.stdout, file=out)
                    continue

                if pass_env:
                    env = os.environ.copy()
                else:
                    env = {}
                env.update(global_env)
                env.update(group.env)

                # Run the test in the venv.
                cmd = venv_command(venv, group.command)
                env_str = " ".join("%s=%s" % (k, v) for k, v in env.items())
                logger.info("Running group command '%s' with environment '%s'.", group.command, env_str)
                # Pipe the command output directly to `out` since we
                # don't need to store it.
                r = subprocess.run(cmd, stdout=out, shell=True, env=env)
                results.append(
                    Dict(name=group.name, depstr=dep_str, venv=venv, returncode=r.returncode, stdout=r.stdout)
                )

        print("\n\n-------------------summary-------------------", file=out)
        for r in results:
            failed = r.returncode != 0
            status_char = "❌" if failed else "✅"
            s = "%s %s: %s" % (status_char, r.name, r.depstr)
            print(s, file=out)

        if any(True for r in results if r.returncode != 0):
            sys.exit(1)


def list_groups(groups, match_str, out=sys.stdout):
    current_group = None
    for group, pyversion, deps in groups_iter(groups):
        if group != current_group:
            current_group = group
            print("%s:" % group.name, file=out)
        deps_str = " ".join("'%s%s'" % (name, version) for name, version in deps)
        py_str = "Python %s" % pyversion
        print("  %s %s" % (py_str, deps_str), file=out)


def generate_base_venvs(groups):
    """Generate all the required base venvs for `groups`.

    This is useful for CI where we can pre-compute all the venvs and
    distribute them to group-specific runners.
    """
    # Find all the
    required_pythons = set([py for g in groups for py in g.pys])
    print(required_pythons)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A simple Python test matrix runner.")
    parser.add_argument(
        "case_matcher", type=str, default=".*", nargs="?", help="Regular expression used to match group names."
    )
    parser.add_argument("-l", "--list", action="store_true", help="List the groups.")
    parser.add_argument(
        "-g", "--generate-base-venvs", action="store_true", help="Generates the base virtual environments."
    )
    parser.add_argument(
        "--pass-env", default=os.getenv("PASS_ENV"), help="Pass the current environment to the test cases."
    )
    parser.add_argument(
        "-s",
        "--skip-base-install",
        default=os.getenv("SKIP_BASE_INSTALL"),
        action="store_true",
        dest="skip_base_install",
        help="Skip installing the dev package and global dependencies into the environment.",
    )
    parser.add_argument("-v", "--verbose", action="store_const", dest="loglevel", const=logging.INFO)
    parser.add_argument("-d", "--debug", action="store_const", dest="loglevel", const=logging.DEBUG)
    args = parser.parse_args()

    if args.loglevel:
        logging.basicConfig(level=args.loglevel)

    logger.debug("Parsed arguments: %r.", args)

    try:
        if args.list:
            list_groups(all_groups, args.case_matcher)
        elif args.generate_base_venvs:
            generate_base_venvs(all_groups)
        else:
            run_groups(all_groups, args.case_matcher, skip_base_install=args.skip_base_install, pass_env=args.pass_env)
    except KeyboardInterrupt as e:
        pass

    sys.exit(0)
