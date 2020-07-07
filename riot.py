"""
TODO
- json report for verify_all_tests
- timing metadata (how long it takes to setup and run a suite, case, etc)
- format/naming convention for cases
- parallelize virtualenv creation - there's no reason to not to
- intermediate venv building eg. if cases share deps X, Y, Z, generate an env
  for these and copy it
- does specifying encoding="utf-8" affect the locale of the test cases?
- reporting to datadog (stats, traces)
- typing
- clean up into classes w/ methods?
- better log formatter / output
- better handling of CmdFailure in run_suites
- add command to run pytest (or any command) in a venv
  eg: python riot.py --suite tracer --python 3.7 --command pytest -k test_tracer_fork
"""
import argparse
import itertools
import logging
import os
import re
import shutil
import subprocess
import sys
import typing as t


logger = logging.getLogger(__name__)


SHELL = "/bin/bash"
ENCODING = "utf-8"


class AttrDict(dict):
    def __init__(self, *args, **kwargs):
        super(AttrDict, self).__init__(*args, **kwargs)
        self.__dict__ = self


class CmdFailure(Exception):
    def __init__(self, msg, completed_proc):
        self.msg = msg
        self.proc = completed_proc
        self.code = completed_proc.returncode
        super().__init__(self, msg)


# It's easier to read `Suite`, `Case` rather than AttrDict or dict.
CaseInstance = Case = Suite = AttrDict

global_deps = [
    "mock",
    "opentracing",
    "pytest<4",
    "pytest-benchmark",
    "pytest-django",
]

global_env = [("PYTEST_ADDOPTS", "--color=yes")]

all_suites = [
    Suite(
        name="tracer",
        command="pytest tests/test_tracer.py",
        env=dict(),
        cases=[
            Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8,], pkgs=[("msgpack", [None, "==0.5.0", ">=0.5,<0.6", ">=0.6.0,<1.0"])],),
        ],
    ),
    Suite(
        name="redis",
        command="pytest tests/contrib/redis/",
        cases=[
            Case(
                pys=[2.7, 3.5, 3.6, 3.7, 3.8,],
                pkgs=[("redis", [">=2.10,<2.11", ">=3.0,<3.1", ">=3.2,<3.3", ">=3.4,<3.5", ">=3.5,<3.6"])],
            ),
        ],
    ),
    Suite(
        name="profiling",
        command="python -m tests.profiling.run pytest --capture=no --verbose tests/profiling/",
        env=[("DD_PROFILE_TEST_GEVENT", lambda c: "1" if "gevent" in c.pkgs else None),],
        cases=[
            Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8], pkgs=[("gevent", [None, ""])],),
            # Min reqs tests
            Case(pys=[2.7], pkgs=[("gevent", ["==1.1.0"]), ("protobuf", ["==3.0.0"]), ("tenacity", ["==5.0.1"]),]),
            Case(
                pys=[3.5, 3.6, 3.7, 3.8],
                pkgs=[("gevent", ["==1.4.0"]), ("protobuf", ["==3.0.0"]), ("tenacity", ["==5.0.1"]),],
            ),
        ],
    ),
    Suite(
        name="django",
        command="pytest tests/contrib/django",
        cases=[
            Case(
                env=[("TEST_DATADOG_DJANGO_MIGRATION", [None, "1"])],
                pys=[2.7, 3.5, 3.6],
                pkgs=[
                    ("django-pylibmc", [">=0.6,<0.7"]),
                    ("django-redis", [">=4.5,<4.6"]),
                    ("pylibmc", [""]),
                    ("python-memcached", [""]),
                    ("django", [">=1.8,<1.9", ">=1.11,<1.12"]),
                ],
            ),
            Case(
                env=[("TEST_DATADOG_DJANGO_MIGRATION", [None, "1"])],
                pys=[3.5],
                pkgs=[
                    ("django-pylibmc", [">=0.6,<0.7"]),
                    ("django-redis", [">=4.5,<4.6"]),
                    ("pylibmc", [""]),
                    ("python-memcached", [""]),
                    ("django", [">=2.0,<2.1", ">=2.1,<2.2"]),
                ],
            ),
            Case(
                env=[("TEST_DATADOG_DJANGO_MIGRATION", [None, "1"])],
                pys=[3.6, 3.7, 3.8],
                pkgs=[
                    ("django-pylibmc", [">=0.6,<0.7"]),
                    ("django-redis", [">=4.5,<4.6"]),
                    ("pylibmc", [""]),
                    ("python-memcached", [""]),
                    ("django", [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3", ">=3.0,<3.1"]),
                ],
            ),
        ],
    ),
]


def rmchars(chars: t.List[str], s: str):
    for c in chars:
        s = s.replace(c, "")
    return s


def get_pep_dep(libname: str, version: str):
    """Returns a valid PEP 508 dependency string.

    ref: https://www.python.org/dev/peps/pep-0508/
    """
    return "%s%s" % (libname, version)


def get_env_str(envs: t.List[t.Tuple]):
    return " ".join("%s=%s" % (k, v) for k, v in envs)


def get_base_venv_path(pyversion):
    """Given a python version return the base virtual environment path relative
    to the current directory.
    """
    return ".venv_py%s" % str(pyversion).replace(".", "")


def run_cmd(*args, **kwargs):
    # Provide our own defaults.
    if "shell" in kwargs and "executable" not in kwargs:
        kwargs["executable"] = SHELL
    if "encoding" not in kwargs:
        kwargs["encoding"] = ENCODING
    if "stdout" not in kwargs:
        kwargs["stdout"] = subprocess.PIPE

    logger.debug("Running command %s", args[0])
    r = subprocess.run(*args, **kwargs)
    logger.debug(r.stdout)

    if r.returncode != 0:
        raise CmdFailure("Command %s failed with code %s." % (args[0], r.returncode), r)
    return r


def create_base_venv(pyversion, path=None, recreate=True):
    """Attempts to create a virtual environment for `pyversion`.

    :param pyversion: string or int representing the major.minor Python
                      version. eg. 3.7, "3.8".
    """
    path = path or get_base_venv_path(pyversion)

    if os.path.isdir(path) and not recreate:
        logger.info("Skipping creating virtualenv %s as it already exists.", path)
        return path

    py_ex = "python%s" % pyversion
    py_ex = shutil.which(py_ex)

    if not py_ex:
        logger.debug("%s interpreter not found", py_ex)
        raise FileNotFoundError
    else:
        logger.info("Found Python interpreter '%s'.", py_ex)

    logger.info("Creating virtualenv '%s' with Python '%s'.", path, py_ex)
    r = run_cmd(["virtualenv", "--python=%s" % py_ex, path], stdout=subprocess.PIPE)
    return path


def get_venv_command(venv_path, cmd):
    """Return the command string used to execute `cmd` in virtual env located
    at `venv_path`.
    """
    return "source %s/bin/activate && %s" % (venv_path, cmd)


def run_cmd_venv(venv, cmd, **kwargs):
    env = kwargs.get("env") or {}
    env_str = " ".join("%s=%s" % (k, v) for k, v in env.items())
    cmd = get_venv_command(venv, cmd)

    logger.debug("Executing command '%s' with environment '%s'", cmd, env_str)
    r = run_cmd(cmd, shell=True, **kwargs)
    return r


def expand_specs(specs):
    """Generator over all configurations of a specification.

    [(X, [X0, X1, ...]), (Y, [Y0, Y1, ...)] ->
      ((X, X0), (Y, Y0)), ((X, X0), (Y, Y1)), ((X, X1), (Y, Y0)), ((X, X1), (Y, Y1))
    """
    all_vals = []

    for name, vals in specs:
        all_vals.append([(name, val) for val in vals])

    all_vals = itertools.product(*all_vals)
    return all_vals


def case_iter(case):
    # We could itertools.product here again, but I think this is clearer.
    for env_cfg in expand_specs(case.env) if "env" in case else [[]]:
        for py in case.pys:
            for pkg_cfg in expand_specs(case.pkgs):
                yield env_cfg, py, pkg_cfg


def cases_iter(cases):
    """Iterator over all case instances of a suite.

    Yields the dependencies unique to the instance of the suite.
    """
    for case in cases:
        for env_cfg, py, pkg_cfg in case_iter(case):
            yield case, env_cfg, py, pkg_cfg


def suites_iter(suites, pattern, py=None):
    """Iterator over an iterable of suites.

    :param py: An optional python version to match against.
    :param pattern: An optional pattern to match suite names against.
    """
    for suite in suites:
        if not pattern.match(suite.name):
            logger.debug("Skipping suite '%s' due to mismatch.", suite.name)
            continue

        for case, env, spy, pkgs in cases_iter(suite.cases):
            if py and spy != py:
                continue
            yield CaseInstance(suite=suite, case=case, env=env, py=spy, pkgs=pkgs)


def run_suites(
    suites, pattern, skip_base_install=False, recreate_venvs=False, out=sys.stdout, pass_env=False,
):
    """Runs the command for each case in `suites` in a unique virtual
    environment.
    """
    results = []

    # Generate the base virtual envs required for the test suites.
    # TODO: errors currently go unhandled
    generate_base_venvs(suites, pattern, recreate=recreate_venvs, skip_deps=skip_base_install)

    for case in suites_iter(suites, pattern=pattern):
        base_venv = get_base_venv_path(case.py)

        # Resolve the packages required for this case.
        pkgs: t.Dict[str, str] = {name: version for name, version in case.pkgs if version is not None}

        # Strip special characters for the venv directory name.
        venv = "_".join(["%s%s" % (n, rmchars("<=>.,", v)) for n, v in pkgs.items()])
        venv = "%s_%s" % (base_venv, venv)
        pkg_str = " ".join(["'%s'" % get_pep_dep(lib, version) for lib, version in pkgs.items()])
        # Case result which will contain metadata about the test execution.
        result = AttrDict(case=case, venv=venv, pkgstr=pkg_str)

        try:
            # Copy the base venv to use for this case.
            logger.info("Copying base virtualenv '%s' into case virtual env '%s'.", base_venv, venv)
            try:
                run_cmd(["cp", "-r", base_venv, venv], stdout=subprocess.PIPE)
            except CmdFailure as e:
                raise CmdFailure("Failed to create case virtual env '%s'\n%s" % (venv, e.proc.stdout), e.proc)

            # Install the case dependencies if there are any.
            if pkg_str:
                logger.info("Installing case dependencies %s.", pkg_str)
                try:
                    run_cmd_venv(venv, "pip install %s" % pkg_str)
                except CmdFailure as e:
                    raise CmdFailure("Failed to install case dependencies %s\n%s" % (pkg_str, e.proc.stdout), e.proc)

            # Generate the environment for the test case.
            env = os.environ.copy() if pass_env else {}
            env.update({k: v for k, v in global_env})

            # Add in the suite env vars.
            for k, v in case.suite.env if "env" in case.suite else []:
                resolved_val = v(AttrDict(pkgs=pkgs)) if callable(v) else v
                if resolved_val is not None:
                    if k in env:
                        logger.debug("Suite overrides environment variable %s", k)
                    env[k] = resolved_val

            # Add in the case env vars.
            for k, v in case.env:
                resolved_val = v(AttrDict(pkgs=pkgs)) if callable(v) else v
                if resolved_val is not None:
                    if k in env:
                        logger.debug("Case overrides environment variable %s", k)
                    env[k] = resolved_val

            # Finally, run the test in the venv.
            cmd = case.suite.command
            env_str = " ".join("%s=%s" % (k, v) for k, v in env.items())
            logger.info("Running suite command '%s' with environment '%s'.", cmd, env_str)
            try:
                # Pipe the command output directly to `out` since we
                # don't need to store it.
                run_cmd_venv(venv, cmd, stdout=out, env=env)
            except CmdFailure as e:
                raise CmdFailure("Test failed with exit code %s" % e.proc.returncode, e.proc)
        except CmdFailure as e:
            result.code = e.code
            print(e.msg, file=out)
        except KeyboardInterrupt:
            result.code = 1
            break
        except Exception as e:
            logger.error("Test runner failed: %s", e, exc_info=True)
            sys.exit(1)
        else:
            result.code = 0
        finally:
            results.append(result)

    print("\n-------------------summary-------------------", file=out)
    for r in results:
        failed = r.code != 0
        status_char = "❌" if failed else "✅"
        env_str = get_env_str(case.env)
        s = "%s %s: %s python%s %s" % (status_char, r.case.suite.name, env_str, r.case.py, r.pkgstr)
        print(s, file=out)

    if any(True for r in results if r.code != 0):
        sys.exit(1)


def list_suites(suites, pattern, out=sys.stdout):
    curr_suite = None
    for case in suites_iter(suites, pattern):
        if case.suite != curr_suite:
            curr_suite = case.suite
            print("%s:" % case.suite.name, file=out)
        pkgs_str = " ".join("'%s'" % get_pep_dep(name, version) for name, version in case.pkgs)
        env_str = get_env_str(case.env)
        py_str = "Python %s" % case.py
        print(" %s %s %s" % (env_str, py_str, pkgs_str), file=out)


def generate_base_venvs(suites, pattern, recreate, skip_deps):
    """Generate all the required base venvs for `suites`.
    """
    # Find all the python versions used.
    required_pys = set([case.py for case in suites_iter(suites, pattern=pattern)])

    logger.info("Generating virtual environments for Python versions %s", " ".join(str(s) for s in required_pys))

    for py in required_pys:
        try:
            venv_path = create_base_venv(py, recreate=recreate)
        except CmdFailure as e:
            logger.error("Failed to create virtual environment.\n%s", e.proc.stdout)
        except FileNotFoundError:
            logger.error("Python version '%s' not found.", py)
        else:
            if skip_deps:
                logger.info("Skipping global deps install.")
                continue

            # Install the global dependencies into the base venv.
            global_deps_str = " ".join(["'%s'" % dep for dep in global_deps])
            logger.info("Installing base dependencies %s into virtualenv.", global_deps_str)

            try:
                run_cmd_venv(venv_path, "pip install %s" % global_deps_str)
            except CmdFailure as e:
                logger.error("Base dependencies failed to install, aborting!\n%s", e.proc.stdout)
                sys.exit(1)

            # Install the dev package into the base venv.
            logger.info("Installing dev package.")
            try:
                run_cmd_venv(venv_path, "pip install -e .")
            except CmdFailure as e:
                logger.error("Dev install failed, aborting!\n%s", e.proc.stdout)
                sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A simple Python test matrix runner.")
    parser.add_argument(
        "case_matcher", type=str, default=".*", nargs="?", help="Regular expression used to match case names."
    )
    parser.add_argument("-l", "--list", action="store_true", help="List the cases.")
    parser.add_argument(
        "-g",
        "--generate-base-venvs",
        action="store_true",
        help="Generates the base virtual environments containing the dev package.",
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
    parser.add_argument(
        "-r",
        "--recreate-venvs",
        default=os.getenv("RECREATE_VENVS"),
        action="store_true",
        dest="recreate_venvs",
        help="Forces the recreation of virtual environments if they already exist.",
    )
    parser.add_argument("-v", "--verbose", action="store_const", dest="loglevel", const=logging.INFO)
    parser.add_argument("-d", "--debug", action="store_const", dest="loglevel", const=logging.DEBUG)
    args = parser.parse_args()

    if args.loglevel:
        logging.basicConfig(level=args.loglevel)

    logger.debug("Parsed arguments: %r.", args)

    pattern = re.compile(args.case_matcher)

    try:
        if args.list:
            list_suites(all_suites, pattern)
        elif args.generate_base_venvs:
            generate_base_venvs(all_suites, pattern)
        else:
            run_suites(
                suites=all_suites,
                pattern=pattern,
                recreate_venvs=args.recreate_venvs,
                skip_base_install=args.skip_base_install,
                pass_env=args.pass_env,
            )
    except KeyboardInterrupt as e:
        pass

    sys.exit(0)
