import itertools
import logging
import os
import shutil
import subprocess

import click
import tabulate

logger = logging.getLogger(__name__)


class Dict(dict):
    def __init__(self, *args, **kwargs):
        super(Dict, self).__init__(*args, **kwargs)
        self.__dict__ = self


global_deps = [
    "mock",
    "opentracing",
    "pytest",
    "pytest-benchmark",
]

global_env = Dict(PYTEST_ADDOPTS="--color=yes",)


configs = [
    Dict(
        name="redis",
        # pys=[2.7, 3.5, 3.6, 3.7, 3.8,],
        pys=[3.7,],
        deps=[("redis", ">=2.10,<2.11")],
        command="pytest tests/contrib/redis/",
        env=dict(),
    ),
]


def run_in_venv(venv, cmd, out=False, env=None):
    env = env or {}

    env_str = " ".join("%s=%s" % (k, v) for k, v in env.items())
    cmd = "source %s/bin/activate && %s" % (venv, cmd)
    print("%s %s" % (env_str, cmd))
    r = subprocess.run(cmd, stdout=subprocess.PIPE, shell=True, env=env)
    if out:
        print(r.stdout.decode("ascii"))
    return r


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

                def rmchars(chars, s):
                    for c in chars:
                        s = s.replace(c, "")
                    return s

                # Strip the special characters
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

                results.append(Dict(name=c.name, returncode=r.returncode,))

        print(results)


@click.command()
@click.option("--case", default=".*", help="A regular expression matching the cases to run")
@click.option("--list", default=False, help="List the test cases and their environments")
def riot():
    run_configurations(configs)


if __name__ == "__main__":
    riot()
