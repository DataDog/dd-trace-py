import argparse
import importlib.abc
import importlib.util
import itertools
import logging
import os
import re
import shutil
import subprocess
import sys
import typing as t

import attr
try:
    import ddtrace

    tracer = ddtrace.tracer
    ddtrace.config.service = "ddtracepy-ci"
except ImportError:

    class NoopSpan:
        def __enter__(self):
            pass

        def __exit__(self, type, value, traceback):
            pass

    class NoopTracer:
        def trace(*args, **kwargs):
            return NoopSpan()

    tracer = NoopTracer()


logger = logging.getLogger(__name__)


SHELL = "/bin/bash"
ENCODING = "utf-8"


class AttrDict(dict):
    def __init__(self, *args, **kwargs):
        super(AttrDict, self).__init__(*args, **kwargs)
        self.__dict__ = self


CaseInstance = Case = Suite = AttrDict


class CmdFailure(Exception):
    def __init__(self, msg, completed_proc):
        self.msg = msg
        self.proc = completed_proc
        self.code = completed_proc.returncode
        super().__init__(self, msg)


@attr.s
class Session:
    suites: t.List[AttrDict] = attr.ib(factory=list)
    global_deps: t.List[str] = attr.ib(factory=list)
    global_env: t.List[t.Tuple[str, str]] = attr.ib(factory=list)

    @classmethod
    def from_config_file(cls, path: str) -> "Session":
        spec = importlib.util.spec_from_file_location("riotfile", path)
        config = importlib.util.module_from_spec(spec)

        # DEV: MyPy has `ModuleSpec.loader` as `Optional[_Loader`]` which doesn't have `exec_module`
        # https://github.com/python/typeshed/blob/fe58699ca5c9ee4838378adb88aaf9323e9bbcf0/stdlib/3/_importlib_modulespec.pyi#L13-L44
        t.cast(importlib.abc.Loader, spec.loader).exec_module(config)

        suites = getattr(config, "suites", [])
        global_deps = getattr(config, "global_deps", [])
        global_env = getattr(config, "global_env", [])

        return cls(suites=suites, global_deps=global_deps, global_env=global_env)

    def run_suites(
        self,
        pattern,
        skip_base_install=False,
        recreate_venvs=False,
        out=sys.stdout,
        pass_env=False,
    ):
        """Runs the command for each case in `suites` in a unique virtual
        environment.
        """
        with tracer.trace("run_suites", resource=pattern.pattern) as span:
            span.set_tags(
                dict(
                    pattern=pattern.pattern,
                    num_suites=len(self.suites),
                    skip_base_install=skip_base_install,
                    recreate_venvs=recreate_venvs,
                    pass_env=pass_env,
                )
            )
            results = []

            # Generate the base virtual envs required for the test suites.
            # TODO: errors currently go unhandled
            self.generate_base_venvs(
                pattern, recreate=recreate_venvs, skip_deps=skip_base_install
            )

            for case in suites_iter(self.suites, pattern=pattern):
                with tracer.trace("run_suite", service=f"{case.suite.name}") as span:
                    span.set_tag("suite", case.suite.name)
                    base_venv = get_base_venv_path(case.py)

                    # Resolve the packages required for this case.
                    pkgs: t.Dict[str, str] = {
                        name: version for name, version in case.pkgs if version is not None
                    }

                    # Strip special characters for the venv directory name.
                    venv = "_".join(
                        ["%s%s" % (n, rmchars("<=>.,", v)) for n, v in pkgs.items()]
                    )
                    venv = "%s_%s" % (base_venv, venv)
                    pkg_str = " ".join(
                        ["'%s'" % get_pep_dep(lib, version) for lib, version in pkgs.items()]
                    )
                    span.set_tag("venv", venv)
                    span.set_tag("pkgs", pkg_str)
                    # Case result which will contain metadata about the test execution.
                    result = AttrDict(case=case, venv=venv, pkgstr=pkg_str)

                    try:
                        # Copy the base venv to use for this case.
                        logger.info(
                            "Copying base virtualenv '%s' into case virtual env '%s'.",
                            base_venv,
                            venv,
                        )
                        try:
                            run_cmd(["cp", "-r", base_venv, venv], stdout=subprocess.PIPE)
                        except CmdFailure as e:
                            raise CmdFailure(
                                "Failed to create case virtual env '%s'\n%s"
                                % (venv, e.proc.stdout),
                                e.proc,
                            )

                        # Install the case dependencies if there are any.
                        if pkg_str:
                            logger.info("Installing case dependencies %s.", pkg_str)
                            try:
                                run_cmd_venv(venv, "pip install %s" % pkg_str)
                            except CmdFailure as e:
                                raise CmdFailure(
                                    "Failed to install case dependencies %s\n%s"
                                    % (pkg_str, e.proc.stdout),
                                    e.proc,
                                )

                        # Generate the environment for the test case.
                        env = os.environ.copy() if pass_env else {}
                        env.update({k: v for k, v in self.global_env})

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
                        logger.info(
                            "Running suite command '%s' with environment '%s'.", cmd, env_str
                        )
                        try:
                            # Pipe the command output directly to `out` since we
                            # don't need to store it.
                            run_cmd_venv(venv, cmd, stdout=out, env=env)
                        except CmdFailure as e:
                            raise CmdFailure(
                                "Test failed with exit code %s" % e.proc.returncode, e.proc
                            )
                    except CmdFailure as e:
                        span.error = 1
                        span.set_tag("error_message", e.msg)
                        span.set_tag("stdout", e.proc.stdout)
                        span.set_tag("stderr", e.proc.stderr)
                        span.set_tag("returncode", e.code)
                        result.code = e.code
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
                s = "%s %s: %s python%s %s" % (
                    status_char,
                    r.case.suite.name,
                    env_str,
                    r.case.py,
                    r.pkgstr,
                )
                print(s, file=out)

            if any(True for r in results if r.code != 0):
                sys.exit(1)

    def list_suites(self, pattern, out=sys.stdout):
        curr_suite = None
        for case in suites_iter(self.suites, pattern):
            if case.suite != curr_suite:
                curr_suite = case.suite
                print("%s:" % case.suite.name, file=out)
            pkgs_str = " ".join(
                "'%s'" % get_pep_dep(name, version) for name, version in case.pkgs
            )
            env_str = get_env_str(case.env)
            py_str = "Python %s" % case.py
            print(" %s %s %s" % (env_str, py_str, pkgs_str), file=out)

    def generate_base_venvs(self, pattern, recreate, skip_deps):
        """Generate all the required base venvs for `suites`.
        """
        with tracer.trace("generate_base_venvs") as span:
            # Find all the python versions used.
            required_pys = set(
                [case.py for case in suites_iter(self.suites, pattern=pattern)]
            )
            span.set_tag("required_pys", required_pys)

            logger.info(
                "Generating virtual environments for Python versions %s",
                " ".join(str(s) for s in required_pys),
            )

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

                    # Install the global package dependencies into the base venv.
                    global_deps_str = " ".join(["'%s'" % dep for dep in self.global_deps])
                    span.set_tag("global_pkgs", global_deps_str)
                    logger.info(
                        "Installing base dependencies %s into virtualenv.", global_deps_str
                    )

                    try:
                        run_cmd_venv(venv_path, "pip install %s" % global_deps_str)
                    except CmdFailure as e:
                        logger.error(
                            "Base dependencies failed to install, aborting!\n%s",
                            e.proc.stdout,
                        )
                        sys.exit(1)

                    # Install the dev package into the base venv.
                    logger.info("Installing dev package.")
                    try:
                        run_cmd_venv(venv_path, "pip install -e .")
                    except CmdFailure as e:
                        logger.error("Dev install failed, aborting!\n%s", e.proc.stdout)
                        sys.exit(1)


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
    with tracer.trace("run_cmd") as span:
        span.set_tag("command", args[0])
        # TODO? maybe not great to report the entire env (will include
        # the user's environment if they pass through)
        span.set_tag("environment", kwargs.get("env"))
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
            raise CmdFailure(
                "Command %s failed with code %s." % (args[0], r.returncode), r
            )
        return r


def create_base_venv(pyversion, path=None, recreate=True):
    """Attempts to create a virtual environment for `pyversion`.

    :param pyversion: string or int representing the major.minor Python
                      version. eg. 3.7, "3.8".
    """
    with tracer.trace("create_base_venv") as span:
        span.set_tag("python_version", pyversion)
        span.set_tag("recreate", recreate)
        span.set_tag("path", path)
        path = path or get_base_venv_path(pyversion)

        if os.path.isdir(path) and not recreate:
            logger.info("Skipping creating virtualenv %s as it already exists.", path)
            return path

        py_ex = "python%s" % pyversion
        py_ex = shutil.which(py_ex)
        span.set_tag("python_executable", py_ex)

        if not py_ex:
            logger.debug("%s interpreter not found", py_ex)
            raise FileNotFoundError
        else:
            logger.info("Found Python interpreter '%s'.", py_ex)

        logger.info("Creating virtualenv '%s' with Python '%s'.", path, py_ex)
        cmd = ["virtualenv", "--python=%s" % py_ex, path]
        span.set_tag("command", " ".join(cmd))
        r = run_cmd(cmd, stdout=subprocess.PIPE)
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


def main():
    parser = argparse.ArgumentParser(description="A simple Python test runner runner.")
    parser.add_argument(
        "case_matcher",
        type=str,
        default=".*",
        nargs="?",
        help="Regular expression used to match case names.",
    )
    parser.add_argument(
        "-f", "--file", type=str, dest="file", default="riotfile.py",
    )
    parser.add_argument("-l", "--list", action="store_true", help="List the cases.")
    parser.add_argument(
        "-g",
        "--generate-base-venvs",
        action="store_true",
        help="Generates the base virtual environments containing the dev package.",
    )
    parser.add_argument(
        "--pass-env",
        default=os.getenv("PASS_ENV") or True,
        help="Pass the current environment to the test cases.",
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
    parser.add_argument(
        "-v", "--verbose", action="store_const", dest="loglevel", const=logging.INFO
    )
    parser.add_argument(
        "-d", "--debug", action="store_const", dest="loglevel", const=logging.DEBUG
    )
    args = parser.parse_args()

    if args.loglevel:
        logging.basicConfig(level=args.loglevel)

    logger.debug("Parsed arguments: %r.", args)

    pattern = re.compile(args.case_matcher)

    # Load riotfile config
    session = Session.from_config_file(args.file)

    try:
        if args.list:
            session.list_suites(pattern)
        elif args.generate_base_venvs:
            session.generate_base_venvs(pattern, recreate=args.recreate_venvs, skip_deps=False)
        else:
            session.run_suites(
                pattern=pattern,
                recreate_venvs=args.recreate_venvs,
                skip_base_install=args.skip_base_install,
                pass_env=args.pass_env,
            )
    except KeyboardInterrupt as e:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
