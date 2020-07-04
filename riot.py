import argparse
import itertools
import logging
import os
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
        # pys=[2.7, 3.5, 3.6, 3.7, 3.8,],
        pys=[3.7,],
        deps=[("redis", ">=2.10,<2.11")],
        # deps=[("redis", ">=2.10,<2.11 >=3.0,<3.1 >=3.2,<3.3 >=3.4,<3.5 >=3.5,<3.6")],
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
    all_deps = []
    for lib, deps in group.deps:
        all_deps.append([(lib, v) for v in deps.split(" ")])

    all_deps = itertools.product(*all_deps)
    for deps in all_deps:
        yield deps


def groups_iter(groups, py=None, pattern="*"):
    # Iterator over the groups
    for g in groups:
        for gpy in g.pys:
            if py and gpy != py:
                continue
            for deps in group_iter(g):
                yield g, gpy, deps


def run_groups(groups, cmd="*", skip_install=False, out=sys.stdout, encoding="utf-8"):
    results = []

    for group in groups:
        for py in group.pys:
            venv_base = ".venv_py%s" % str(py).replace(".", "")
            py_ex = "python%s" % py
            py_ex = shutil.which(py_ex)

            if not py_ex:
                print("%s interpreter not found" % py_ex)
                sys.exit(1)
            else:
                logger.info("Found Python interpreter '%s'.", py_ex)

            if os.path.isdir(venv_base):
                if not skip_install:
                    logger.info("Installing dev package.")
                    r = run_in_venv(venv_base, "pip install -e .")
            else:
                # Create the base virtual env and install the package
                logger.info("Creating virtualenv '%s' with Python '%s'.", venv_base, py_ex)
                r = subprocess.run(["virtualenv", "--python=%s" % py_ex, venv_base], stdout=subprocess.PIPE, encoding=encoding)
                if r.returncode:
                    print(r.stdout, file=out)
                    sys.exit(1)

                global_deps_str = " ".join(["'%s'" % dep for dep in global_deps])
                logger.info("Installing global dependencies into virtualenv '%s'.", global_deps_str)
                r = run_in_venv(venv_base, "pip install %s" % global_deps_str)

                logger.info("Installing dev package.")
                r = run_in_venv(venv_base, "pip install -e .")

            for deps in group_iter(group):
                # Strip special characters for the directory name
                venv = "_".join(["%s%s" % (lib, rmchars("<=>.,", vers)) for lib, vers in deps])
                venv = "%s_%s" % (venv_base, venv)

                logger.info("Copying base virtualenv '%s' into group virtual env '%s'.", venv_base, venv)
                r = subprocess.run(["cp", "-r", venv_base, venv], stdout=subprocess.PIPE)

                dep_str = " ".join(["'%s%s'" % (lib, version) for lib, version in deps])
                logger.info("Installing group dependencies %s.", dep_str)
                r = run_in_venv(venv, "pip install %s" % dep_str)
                if r.returncode != 0:
                    print(r.stdout, file=out)
                    continue

                # TODO: option to pass-through os.environ
                env = global_env.copy()
                env.update(group.env)

                # Run the test
                cmd = venv_command(venv, group.command)
                # Pipe the output directly to out
                logger.info("Running group command '%s'.", group.command)
                r = subprocess.run(cmd, stdout=out, shell=True, env=env)
                results.append(Dict(name=group.name, depstr=dep_str, venv=venv, returncode=r.returncode, stdout=r.stdout))


        print("\n\n-------------------summary-------------------", file=out)
        for r in results:
            failed = r.returncode != 0
            status_char = "❌" if failed else "✅"
            s = "%s %s: %s" % (status_char, r.name, r.depstr)
            print(s, file=out)

        err = any(True for r in results if r.returncode != 0)
        if err:
            sys.exit(1)


def list_groups(groups, match_str):
    for group, py, deps in groups_iter(groups):
        print(group.name + ":", py, " ".join("'%s%s'" % (name, version) for name, version in deps))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A simple Python test matrix runner.")
    parser.add_argument(
        "case_matcher", type=str, default="*", nargs="?", help="List the test cases and their environments."
    )
    parser.add_argument("--list", action="store_true", help="List the test cases and their environments.")
    parser.add_argument(
        "--pass-env", default=os.getenv("PASS_ENV"), help="Pass the current environment to the test cases."
    )
    parser.add_argument(
        "-s",
        "--skip-install",
        default=os.getenv("SKIP_INSTALL"),
        action="store_true",
        dest="skip_install",
        help="Skip installing the package into the environment.",
    )
    parser.add_argument("-v", "--verbose", action="store_const", dest="loglevel", const=logging.INFO)
    parser.add_argument("-d", "--debug", action="store_const", dest="loglevel", const=logging.DEBUG)
    args = parser.parse_args()

    if args.loglevel:
        logging.basicConfig(level=args.loglevel)

    if args.list:
        list_groups(all_groups, args.case_matcher)
        sys.exit(0)

    try:
        run_groups(all_groups, args.case_matcher, skip_install=args.skip_install)
    except KeyboardInterrupt as e:
        pass
