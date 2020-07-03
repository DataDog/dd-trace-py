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
    "pytest",
    "pytest-benchmark",
]

global_env = Dict(PYTEST_ADDOPTS="--color=yes",)


all_clumps = [
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


def run_in_venv(venv, cmd, out=False, env=None):
    env = env or {}

    env_str = " ".join("%s=%s" % (k, v) for k, v in env.items())
    cmd = "source %s/bin/activate && %s" % (venv, cmd)
    print(cmd)
    r = subprocess.run(cmd, stdout=subprocess.PIPE, shell=True, env=env)
    if out:
        print(r.stdout.decode("ascii"))
    return r


def clump_iter(clump):
    all_deps = []
    for lib, deps in clump.deps:
        all_deps.append([(lib, v) for v in deps.split(" ")])

    all_deps = itertools.product(*all_deps)
    for deps in all_deps:
        yield deps



def clumps_iter(clumps, py=None, pattern="*"):
    # Iterator over the clumps
    for c in clumps:
        for cpy in c.pys:
            if py and cpy != py:
                continue
            for deps in clump_iter(c):
                yield c, cpy, deps


def run_configurations(configs, cmd="*"):
    results = []

    for c in configs:
        for py in c.pys:
            venv_base = ".venv_py%s" % str(py).replace(".", "")
            py_ex = "python%s" % py
            py_ex = shutil.which(py_ex)

            if not py_ex:
                print("%s interpreter not found" % py_ex)
                sys.exit(1)

            if os.path.isdir(venv_base):
                # Maybe be smarter about if the package has already been built
                pass
            else:
                # Create the base virtual env and install the package
                r = subprocess.run(["virtualenv", "--python=%s" % py_ex, venv_base], stdout=subprocess.PIPE)
                print(r.stdout)

            global_deps_str = " ".join(global_deps)
            r = run_in_venv(venv_base, "pip install %s" % global_deps_str)
            r = run_in_venv(venv_base, "pip install -e .")

            envs = []
            for lib, deps in c.deps:
                envs.append([(lib, v) for v in deps.split(" ")])
            envs = itertools.product(*envs)

            for env in envs:
                # Strip special characters for the directory name
                venv = "_".join(["%s%s" % (lib, rmchars("<=>.,", vers)) for lib, vers in env])
                venv = "%s_%s" % (venv_base, venv)

                r = subprocess.run(["cp", "-r", venv_base, venv], stdout=subprocess.PIPE)

                dep_str = " ".join(["'%s%s'" % (lib, version) for lib, version in env])
                r = run_in_venv(venv, "pip install %s" % dep_str)
                if r.returncode != 0:
                    print(r.stdout)
                    continue

                # TODO: option to pass-through os.environ
                env = global_env.copy()
                env.update(c.env)
                r = run_in_venv(venv, c.command, out=True, env=env)
                results.append(Dict(name=c.name, returncode=r.returncode, stdout=r.stdout))

        for result in results:
            pass


def list_clumps(clumps, match_str):
    for clump, py, deps in clumps_iter(clumps):
        print(clump.name, py, " ".join("'%s%s'" % (name, version) for (name, version) in deps))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A simple Python test matrix runner.")
    parser.add_argument("case_matcher", type=str, default="*", nargs="?", help="List the test cases and their environments.")
    parser.add_argument("--list", action="store_true", help="List the test cases and their environments.")
    parser.add_argument("--pass-env", default=os.getenv("PASS_ENV"), help="Pass the current environment to the test cases.")
    parser.add_argument("-v", "--verbosity", action="count", default=0)
    args = parser.parse_args()

    if args.list:
        list_clumps(all_clumps, args.case_matcher)
        sys.exit(0)

    run_configurations(all_clumps, args.case_matcher)
