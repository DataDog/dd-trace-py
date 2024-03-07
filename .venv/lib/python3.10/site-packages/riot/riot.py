from contextlib import contextmanager
import dataclasses
import functools
from hashlib import sha256
import importlib.abc
import importlib.util
import itertools
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback
import typing as t

import click
from packaging.version import Version
import pexpect
from rich import print as rich_print
from rich.pretty import Pretty
from rich.status import Status
from rich.table import Table

logger = logging.getLogger(__name__)

DEFAULT_RIOT_PATH = ".riot"
DEFAULT_RIOT_ENV_PREFIX = "venv_py"

SHELL = os.getenv("SHELL", "/bin/bash")
ENCODING = sys.getdefaultencoding()
SHELL_RCFILE = """
source {venv_path}/bin/activate
echo -e "\e[31;1m"
echo "                 )  "
echo " (   (        ( /(  "
echo " )(  )\   (   )\()) "
echo "(()\((_)  )\ (_))/  "
echo " ((_)(_) ((_)| |_   "
echo "| '_|| |/ _ \|  _|  "
echo "|_|  |_|\___/ \__|  "
echo -e "\e[0m"
echo -e "\e[33;1mInteractive shell\e[0m"
echo ""
echo -e "* Venv name   : \e[1m{name}\e[0m"
echo -e "* Venv path   : \e[1m{venv_path}\e[0m"
echo -e "* Interpreter : \e[1m$( python -V )\e[0m"
"""


if t.TYPE_CHECKING or sys.version_info[:2] >= (3, 9):
    _T_CompletedProcess = subprocess.CompletedProcess[str]
else:
    _T_CompletedProcess = subprocess.CompletedProcess


_K = t.TypeVar("_K")
_V = t.TypeVar("_V")


def rm_singletons(d: t.Dict[_K, t.Union[_V, t.List[_V]]]) -> t.Dict[_K, t.List[_V]]:
    """Convert single values in a dictionary to a list with that value.

    >>> rm_singletons({ "k": "v" })
    {'k': ['v']}
    >>> rm_singletons({ "k": ["v"] })
    {'k': ['v']}
    >>> rm_singletons({ "k": ["v", "x", "y"] })
    {'k': ['v', 'x', 'y']}
    >>> rm_singletons({ "k": [1, 2, 3] })
    {'k': [1, 2, 3]}
    """
    return {k: to_list(v) for k, v in d.items()}


def to_list(x: t.Union[_K, t.List[_K]]) -> t.List[_K]:
    """Convert a single value to a list containing that value.

    >>> to_list(["x", "y", "z"])
    ['x', 'y', 'z']
    >>> to_list(["x"])
    ['x']
    >>> to_list("x")
    ['x']
    >>> to_list(1)
    [1]
    """
    return [x] if not isinstance(x, list) else x


_T_stdio = t.Union[None, int, t.IO[t.Any]]


class VenvError(Exception):
    pass


@dataclasses.dataclass(unsafe_hash=True, eq=True)
class Interpreter:
    _T_hint = t.Union[float, int, str]

    hint: dataclasses.InitVar[_T_hint]
    _hint: str = dataclasses.field(init=False)

    def __post_init__(self, hint: _T_hint) -> None:
        """Normalize the data."""
        self._hint = str(hint)

    def __str__(self) -> str:
        """Return the path of the interpreter executable."""
        return repr(self)

    @functools.lru_cache()
    def version(self) -> str:
        path = self.path()

        output = subprocess.check_output(
            [
                path,
                "-c",
                'import sys; print("%s.%s.%s" % (sys.version_info.major, sys.version_info.minor, sys.version_info.micro))',
            ],
        )
        return output.decode().strip()

    @functools.lru_cache()
    def version_info(self) -> t.Tuple[int, int, int]:
        return t.cast(
            t.Tuple[int, int, int], tuple(map(int, self.version().split(".")))
        )

    @property
    def bin_path(self) -> t.Optional[str]:
        return os.path.join(self.venv_path, "bin")

    @property
    def site_packages_path(self) -> str:
        version = ".".join((str(_) for _ in self.version_info()[:2]))
        return os.path.join(self.venv_path, "lib", f"python{version}", "site-packages")

    @functools.lru_cache()
    def path(self) -> str:
        """Return the Python interpreter path or raise.

        This defers the error until the interpeter is actually required. This is
        desirable for cases where a user might not require all the mentioned
        interpreters to be installed for their usage.
        """
        py_ex = shutil.which(self._hint)

        if not py_ex:
            py_ex = shutil.which(f"python{self._hint}")

        if py_ex:
            # Ensure that we are getting the path of the actual executable,
            # rather than some wrapping shell script.

            return os.path.abspath(
                subprocess.check_output(
                    [py_ex, "-c", "import sys;print(sys.executable)"]
                )
                .decode()
                .strip()
            )

        raise FileNotFoundError(f"Python interpreter {self._hint} not found")

    @property
    def venv_path(self) -> str:
        """Return the path to the virtual environment for this interpreter."""
        version = self.version().replace(".", "")
        env_base_path = os.environ.get("RIOT_ENV_BASE_PATH", DEFAULT_RIOT_PATH)
        return os.path.abspath(
            os.path.join(env_base_path, f"{DEFAULT_RIOT_ENV_PREFIX}{version}")
        )

    def exists(self) -> bool:
        """Return whether the virtual environment for this interpreter exists."""
        return os.path.isdir(self.venv_path)

    def create_venv(self, recreate: bool, path: t.Optional[str] = None) -> None:
        """Attempt to create a virtual environment for this intepreter.

        Returns ``True`` if the virtual environment was created or ``False`` if
        it already existed.
        """
        venv_path: str = path or self.venv_path

        if os.path.isdir(venv_path):
            if not recreate:
                logger.info(
                    "Skipping creation of virtualenv '%s' as it already exists.",
                    venv_path,
                )
                return
            logger.info("Deleting virtualenv '%s'", venv_path)
            shutil.rmtree(venv_path)

        py_ex = self.path()
        logger.info("Creating virtualenv '%s' with interpreter '%s'.", venv_path, py_ex)
        run_cmd(
            ["virtualenv", f"--python={py_ex}", venv_path],
            stdout=subprocess.PIPE,
        )


@dataclasses.dataclass
class Venv:
    """Specifies how to build and run a virtual environment.

    Venvs can be nested to benefit from inheriting from a parent Venv. All
    attributes are passed down to child Venvs. The child Venvs can override
    parent attributes with the semantics defined below.

    Example::

        Venv(
          pys=[3.9],
          venvs=[
              Venv(
                  name="mypy",
                  command="mypy",
                  pkgs={
                      "mypy": "==0.790",
                  },
              ),
              Venv(
                  name="test",
                  pys=["3.7", "3.8", "3.9"],
                  command="pytest",
                  pkgs={
                      "pytest": "==6.1.2",
                  },
              ),
          ])

    Args:
        name (str): Name of the instance. Overrides parent value.
        command (str): Command to run in the virtual environment. Overrides parent value.
        pys  (List[float]): Python versions. Overrides parent value.
        pkgs (Dict[str, Union[str, List[str]]]): Packages and version(s) to install into the virtual env. Merges and overrides parent values.
        env  (Dict[str, Union[str, List[str]]]): Environment variables to define in the virtual env. Merges and overrides parent values.
        venvs (List[Venv]): List of Venvs that inherit the properties of this Venv (unless they are overridden).
        create (bool): Create the virtual environment instance. Defaults to ``False``, in which case only a prefix is created.
    """

    pys: dataclasses.InitVar[
        t.Union[Interpreter._T_hint, t.List[Interpreter._T_hint]]
    ] = None
    pkgs: dataclasses.InitVar[t.Dict[str, t.Union[str, t.List[str]]]] = None
    env: dataclasses.InitVar[t.Dict[str, t.Union[str, t.List[str]]]] = None
    name: t.Optional[str] = None
    command: t.Optional[str] = None
    venvs: t.List["Venv"] = dataclasses.field(default_factory=list)
    create: bool = False
    skip_dev_install: bool = False

    def __post_init__(self, pys, pkgs, env):
        """Normalize the data."""
        self.pys = [Interpreter(py) for py in to_list(pys)] if pys is not None else []
        self.pkgs = rm_singletons(pkgs) if pkgs else {}
        self.env = rm_singletons(env) if env else {}

    def instances(
        self,
        parent_inst: t.Optional["VenvInstance"] = None,
    ) -> t.Generator["VenvInstance", None, None]:
        # Expand out the instances for the venv.
        for env_spec in expand_specs(self.env):  # type: ignore[attr-defined]
            # Bubble up env
            env = parent_inst.env.copy() if parent_inst else {}
            env.update(dict(env_spec))

            # Bubble up pys
            pys = self.pys or [parent_inst.py if parent_inst else None]  # type: ignore[attr-defined]

            for py in pys:
                for pkgs in expand_specs(self.pkgs):  # type: ignore[attr-defined]
                    inst = VenvInstance(
                        # Bubble up name and command if not overridden
                        venv=self,
                        py=py,
                        env=env,
                        pkgs=dict(pkgs),
                        parent=parent_inst,
                    )
                    if not self.venvs:
                        yield inst
                    else:
                        for venv in self.venvs:
                            yield from venv.instances(inst)


@contextmanager
def nspkgs(inst: "VenvInstance") -> t.Generator[None, None, None]:
    src_ns_files = {}
    dst_ns_files = []
    moved_ns_files = []

    venv_sitepkgs = inst.py.site_packages_path

    # Collect the namespaces to copy over
    for sitepkgs in (_ for _ in inst.site_packages_list[2:] if _ != venv_sitepkgs):
        try:
            for ns in (_ for _ in os.listdir(sitepkgs) if _.endswith("nspkg.pth")):
                if ns not in src_ns_files:
                    src_ns_files[ns] = sitepkgs
        except FileNotFoundError:
            pass

    # Copy over the namespaces
    for ns, src_sitepkgs in src_ns_files.items():
        src_ns_path = os.path.join(src_sitepkgs, ns)
        dst_ns_path = os.path.join(venv_sitepkgs, ns)

        # if the destination file exists already we make a backup copy as it
        # belongs to the base venv and we don't want to overwrite it
        if os.path.isfile(dst_ns_path):
            shutil.move(dst_ns_path, dst_ns_path + ".bak")
            moved_ns_files.append(dst_ns_path)

        with open(src_ns_path) as ns_in, open(dst_ns_path, "w") as ns_out:
            # https://github.com/pypa/setuptools/blob/b62705a84ab599a2feff059ececd33800f364555/setuptools/namespaces.py#L44
            # TODO: Cache the file content to avoid re-reading it
            ns_out.write(
                ns_in.read().replace(
                    "sys._getframe(1).f_locals['sitedir']",
                    f"'{src_sitepkgs}'",
                )
            )

        dst_ns_files.append(dst_ns_path)

    yield

    # Clean up the base venv
    for ns_file in dst_ns_files:
        os.remove(ns_file)

    for ns_file in moved_ns_files:
        shutil.move(ns_file + ".bak", ns_file)


@dataclasses.dataclass
class VenvInstance:
    venv: Venv
    pkgs: t.Dict[str, str]
    py: Interpreter
    env: t.Dict[str, str]
    parent: t.Optional["VenvInstance"] = None

    def __post_init__(self) -> None:
        """Venv instance post-initialization."""
        self.name: t.Optional[str] = self.venv.name or (
            self.parent.name if self.parent is not None else None
        )
        self.command: t.Optional[str] = self.venv.command or (
            self.parent.command if self.parent is not None else None
        )

        self.created = self.venv.create
        if self.created:
            ancestor = self.parent
            while ancestor:
                for pkg in ancestor.pkgs:
                    if pkg not in self.pkgs:
                        self.pkgs[pkg] = ancestor.pkgs[pkg]
                if ancestor.created:
                    break
                ancestor = ancestor.parent

    def matches_pattern(self, pattern: t.Pattern[str]) -> bool:
        """Return whether this VenvInstance matches the provided pattern.

        The pattern is checked against the instance ``name`` and ``short_hash``.
        """
        if self.name and pattern.match(self.name):
            return True
        return bool(pattern.match(self.short_hash))

    @property
    def prefix(self) -> t.Optional[str]:
        """Return path to directory where dependencies should be installed.

        This will return a python version + package specific path name.
        If no packages are defined it will return ``None``.
        """
        if self.py is None:
            return None

        venv_path = self.py.venv_path
        assert venv_path is not None, self

        ident = self.ident
        assert ident is not None, self
        prefix_path = "_".join((venv_path, ident))
        return (
            "_".join((venv_path, self.long_hash))[:255]
            if len(prefix_path) > 255
            else prefix_path
        )

    @property
    def venv_path(self) -> t.Optional[str]:
        # Try to take the closest created ancestor
        current: t.Optional[VenvInstance] = self
        while current:
            if current.created:
                return current.prefix
            current = current.parent

        # If no created ancestors, return the base venv path
        if self.py is not None:
            return self.py.venv_path

        return None

    @property
    def ident(self) -> t.Optional[str]:
        """Return prefix identifier string based on packages."""
        return "_".join(
            (
                f"{rmchars('<=>.,:+@/', n)}"
                for n in self.full_pkg_str.replace("'", "").split()
            )
        )

    @property
    def pkg_str(self) -> str:
        """Return pip friendly install string from defined packages."""
        return pip_deps(self.pkgs)

    @property
    def full_pkg_str(self) -> str:
        """Return pip friendly install string from defined packages."""
        chain: t.List[VenvInstance] = [self]
        current: t.Optional[VenvInstance] = self
        while current is not None:
            chain.insert(0, current)
            current = current.parent

        pkgs: t.Dict[str, str] = {}
        for inst in chain:
            pkgs.update(dict(inst.pkgs))

        return pip_deps(pkgs)

    @property
    def long_hash(self) -> str:
        return hex(hash(self))[2:]

    @property
    def short_hash(self) -> str:
        return self.long_hash[:7]

    def __hash__(self):
        """Compute a hash for the venv instance."""
        h = sha256()
        h.update(repr(self.name).encode())
        h.update(repr(self.py).encode())
        h.update(self.full_pkg_str.encode())
        return int(h.hexdigest(), 16)

    @property
    def requirements(self) -> str:
        """Requirements for dependencies with pinned versions."""
        # Transform full_pkg_str into requirements.in format
        pkgs = "\n".join(self.full_pkg_str.replace("'", "").split(" "))
        _dir = os.path.join(DEFAULT_RIOT_PATH, "requirements")
        os.makedirs(_dir, exist_ok=True)
        in_path = os.path.join(_dir, "{}.in".format(self.short_hash))
        subprocess.check_output(
            [self.py.path(), "-m", "pip", "install", "pip-tools"],
        )
        # pip==23.2 included a breaking change for pip-tools but not available
        # pip-tools==7.0 fixes this but also dropped support for 3.7
        if self.py.version_info()[:2] == (3, 7):
            subprocess.check_output(
                [self.py.path(), "-m", "pip", "install", "-U", "pip<23.2"],
            )
        cmd = [
            self.py.path(),
            "-m",
            "piptools",
            "compile",
            "-q",
            "--no-annotate",
            in_path,
        ]
        if tuple([int(v) for v in self.py.version().strip("()").split(".")]) >= (3, 7):
            cmd.append("--resolver=backtracking")
        logger.info(
            "Compiling requirements file %s at %s.",
            in_path,
            self.prefix,
        )
        with open(in_path, "w+b") as f:
            f.write(pkgs.encode("utf-8"))
            f.flush()

            out = subprocess.check_output(cmd)

            return out.decode("utf-8")

    @property
    def bin_path(self) -> t.Optional[str]:
        prefix = self.prefix
        if prefix is None:
            return None
        return os.path.join(prefix, "bin")

    @property
    def scriptpath(self):
        paths = []

        current: t.Optional[VenvInstance] = self
        while current is not None and not current.created:
            if current.pkgs:
                assert current.bin_path is not None, current
                paths.append(current.bin_path)
            current = current.parent

        if not self.created and self.py:
            if self.py.bin_path is not None:
                paths.append(self.py.bin_path)

        return ":".join(paths)

    @property
    def site_packages_path(self) -> t.Optional[str]:
        prefix = self.prefix
        if prefix is None:
            return None
        version = ".".join((str(_) for _ in self.py.version_info()[:2]))
        return os.path.join(prefix, "lib", f"python{version}", "site-packages")

    @property
    def site_packages_list(self) -> t.List[str]:
        """Return a list of all the site-packages paths along the parenting relation.

        The list starts with the empty string and is followed by the site-packages path
        of the current instance, then the parent site-packages paths follow.
        """
        paths = ["", os.getcwd()]  # mimick 'python -m'

        current: t.Optional[VenvInstance] = self
        while current is not None and not current.created:
            if current.pkgs:
                assert current.site_packages_path is not None, current
                paths.append(current.site_packages_path)
            current = current.parent

        if not self.created and self.py:
            if self.py.site_packages_path is not None:
                paths.append(self.py.site_packages_path)

        return paths

    @property
    def pythonpath(self) -> str:
        return ":".join(self.site_packages_list)

    def match_venv_pattern(self, pattern: t.Pattern[str]) -> bool:
        current: t.Optional[VenvInstance] = self
        idents = []
        while current is not None:
            ident = current.ident
            if ident is not None:
                idents.append(ident)
            current = current.parent

        if not idents:
            return True

        return bool(pattern.search("_".join(idents[::-1])))

    def prepare(
        self,
        env: t.Dict[str, str],
        py: t.Optional[Interpreter] = None,
        recreate: bool = False,
        skip_deps: bool = False,
        recompile_reqs: bool = False,
        child_was_installed: bool = False,
    ) -> None:
        # Propagate the interpreter down the parenting relation
        self.py = py = py or self.py
        if recompile_reqs:
            recreate = True

        exists = self.prefix is not None and os.path.isdir(self.prefix)

        installed = False
        if (
            py is not None
            and self.prefix is not None
            # We only install dependencies if the prefix directory does not
            # exist already. If it does exist, we assume it is in a good state.
            and (not os.path.isdir(self.prefix) or recreate or recompile_reqs)
            and not child_was_installed
        ):
            venv_path = self.venv_path
            assert venv_path is not None, py

            if self.created:
                py.create_venv(recreate, venv_path)
                if not self.venv.skip_dev_install or not skip_deps:
                    install_dev_pkg(venv_path, force=True)

            pkg_str = self.pkg_str
            assert pkg_str is not None
            compiled_requirements_file = (
                f"{DEFAULT_RIOT_PATH}/requirements/{self.short_hash}.txt"
            )
            if recompile_reqs or not os.path.exists(compiled_requirements_file):
                _ = self.requirements
            cmd = (
                f"pip --disable-pip-version-check install --prefix '{self.prefix}' --no-warn-script-location "
                f"-r {compiled_requirements_file}"
            )
            logger.info(
                "Installing venv dependencies %s at %s.",
                compiled_requirements_file,
                self.prefix,
            )
            try:
                if self.created:
                    deps_venv_path = venv_path
                else:
                    deps_venv_path = venv_path + "_deps"
                    if not Path(deps_venv_path).exists():
                        py.create_venv(recreate=False, path=deps_venv_path)
                Session.run_cmd_venv(deps_venv_path, cmd, env=env)
            except CmdFailure as e:
                raise CmdFailure(
                    f"Failed to install venv dependencies {pkg_str}\n{e.proc.stdout}",
                    e.proc,
                )
            else:
                installed = True

        if not self.created and self.parent is not None:
            self.parent.prepare(
                env, py, child_was_installed=installed or exists or child_was_installed
            )


@dataclasses.dataclass
class VenvInstanceResult:
    instance: VenvInstance
    venv_name: str
    code: int = 1
    output: str = ""


class CmdFailure(Exception):
    def __init__(self, msg, completed_proc):
        self.msg = msg
        self.proc = completed_proc
        self.code = completed_proc.returncode
        super().__init__(self, msg)


@dataclasses.dataclass
class Session:
    venv: Venv
    warnings = (
        "deprecated",
        "deprecation",
        "warning",
        "no longer maintained",
        "not maintained",
        "did you mean",
    )

    ALWAYS_PASS_ENV = {
        "LANG",
        "LANGUAGE",
        "SSL_CERT_FILE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "PIP_INDEX_URL",
        "PATH",
    }

    @classmethod
    def from_config_file(cls, path: str) -> "Session":
        spec = importlib.util.spec_from_file_location("riotfile", path)
        if not spec:
            raise Exception(
                f"Invalid file format for riotfile. Expected file with .py extension got '{path}'."
            )
        config = importlib.util.module_from_spec(spec)

        # DEV: MyPy has `ModuleSpec.loader` as `Optional[_Loader`]` which doesn't have `exec_module`
        # https://github.com/python/typeshed/blob/fe58699ca5c9ee4838378adb88aaf9323e9bbcf0/stdlib/3/_importlib_modulespec.pyi#L13-L44
        try:
            t.cast(importlib.abc.Loader, spec.loader).exec_module(config)
        except Exception as e:
            raise Exception(
                f"Failed to parse riotfile '{path}'.\n{traceback.format_exc()}"
            ) from e
        else:
            venv = getattr(config, "venv", Venv())
            return cls(venv=venv)

    def is_warning(self, output):
        if output is None:
            return False
        lower_output = output.lower()
        return any(warning in lower_output for warning in self.warnings)

    def run(
        self,
        pattern: t.Pattern[str],
        venv_pattern: t.Pattern[str],
        skip_base_install: bool = False,
        recreate_venvs: bool = False,
        out: t.TextIO = sys.stdout,
        pass_env: bool = False,
        cmdargs: t.Optional[t.Sequence[str]] = None,
        pythons: t.Optional[t.Set[Interpreter]] = None,
        skip_missing: bool = False,
        exit_first: bool = False,
        recompile_reqs: bool = False,
    ) -> None:
        results = []

        self.generate_base_venvs(
            pattern,
            recreate=recreate_venvs,
            skip_deps=skip_base_install,
            pythons=pythons,
        )

        for inst in self.venv.instances():
            if inst.command is None:
                logger.debug("Skipping venv instance %s due to missing command", inst)
                continue

            if inst.name and not inst.matches_pattern(pattern):
                logger.debug(
                    "Skipping venv instance %s due to name pattern mismatch.", inst
                )
                continue

            assert inst.py is not None, inst
            if pythons and inst.py not in pythons:
                logger.debug(
                    "Skipping venv instance %s due to interpreter mismatch", inst
                )
                continue

            try:
                venv_path = inst.venv_path
                assert venv_path is not None, inst
            except FileNotFoundError:
                if skip_missing:
                    logger.warning("Skipping missing interpreter %s", inst.py)
                    continue
                else:
                    raise

            if not inst.match_venv_pattern(venv_pattern):
                logger.debug(
                    "Skipping venv instance '%s' due to pattern mismatch", venv_path
                )
                continue

            logger.info("Running with %s", inst.py)

            # Result which will be updated with the test outcome.
            result = VenvInstanceResult(instance=inst, venv_name=venv_path)

            # Generate the environment for the instance.
            if pass_env:
                env = os.environ.copy()
                env.update(dict(inst.env))
            else:
                env = dict(inst.env)

            # Add riot specific environment variables
            env.update(
                {
                    "RIOT": "1",
                    "RIOT_PYTHON_HINT": str(inst.py),
                    "RIOT_PYTHON_VERSION": inst.py.version(),
                    "RIOT_VENV_HASH": inst.short_hash,
                    "RIOT_VENV_IDENT": inst.ident or "",
                    "RIOT_VENV_NAME": inst.name or "",
                    "RIOT_VENV_PKGS": inst.pkg_str,
                    "RIOT_VENV_FULL_PKGS": inst.full_pkg_str,
                }
            )

            inst.prepare(
                env,
                skip_deps=skip_base_install or inst.venv.skip_dev_install,
                recreate=recreate_venvs,
                recompile_reqs=recompile_reqs,
            )

            pythonpath = inst.pythonpath
            if pythonpath:
                env["PYTHONPATH"] = (
                    f"{pythonpath}:{env['PYTHONPATH']}"
                    if "PYTHONPATH" in env
                    else pythonpath
                )
            script_path = inst.scriptpath
            if script_path:
                env["PATH"] = ":".join(
                    (script_path, env.get("PATH", os.environ["PATH"]))
                )

            try:
                # Finally, run the test in the base venv.
                command = inst.command
                assert command is not None
                if cmdargs is not None:
                    command = command.format(
                        cmdargs=(" ".join(f"'{arg}'" for arg in cmdargs))
                    ).strip()
                env_str = "\n".join(f"{k}={v}" for k, v in env.items())
                logger.info(
                    "Running command '%s' in venv '%s' with environment:\n%s.",
                    command,
                    venv_path,
                    env_str,
                )
                with nspkgs(inst):
                    try:
                        output = self.run_cmd_venv(
                            venv_path, command, stdout=out, env=env
                        )
                        result.output = output.stdout
                    except CmdFailure as e:
                        raise CmdFailure(
                            f"Test failed with exit code {e.proc.returncode}", e.proc
                        )
            except CmdFailure as e:
                result.code = e.code
                click.echo(click.style(e.msg, fg="red"))
                if exit_first:
                    break
            except KeyboardInterrupt:
                result.code = 1
                break
            except Exception:
                logger.error("Test runner failed", exc_info=True)
                sys.exit(1)
            else:
                result.code = 0
            finally:
                results.append(result)

        click.echo(
            click.style("\n-------------------summary-------------------", bold=True)
        )

        num_failed = 0
        num_passed = 0
        num_warnings = 0

        for r in results:
            failed = r.code != 0
            env_str = env_to_str(r.instance.env)
            s = f"{r.instance.name}: [{r.instance.short_hash}] {env_str} python{r.instance.py} {r.instance.full_pkg_str}"

            if failed:
                num_failed += 1
                s = f"{click.style('x', fg='red', bold=True)} {click.style(s, fg='red')}"
                click.echo(s)
            else:
                num_passed += 1
                if self.is_warning(r.output):
                    num_warnings += 1
                    s = f"{click.style('⚠', fg='yellow', bold=True)} {click.style(s, fg='yellow')}"
                    click.echo(s)
                else:
                    s = f"{click.style('✓', fg='green', bold=True)} {click.style(s, fg='green')}"
                    click.echo(s)

        s_num = f"{num_passed} passed with {num_warnings} warnings, {num_failed} failed"
        click.echo(click.style(s_num, fg="blue", bold=True))

        if any(True for r in results if r.code != 0):
            sys.exit(1)

    def list_venvs(
        self,
        pattern,
        venv_pattern,
        pythons=None,
        out=sys.stdout,
        pipe_mode=False,
        interpreters=False,
        hash_only=False,
    ):
        python_interpreters = set()
        venv_hashes = set()
        table = None
        if not (pipe_mode or interpreters or hash_only):
            table = Table(
                "No.",
                "Hash",
                "Name",
                "Interpreter",
                "Environment",
                "Packages",
                box=None,
            )

        for n, inst in enumerate(self.venv.instances()):
            if not inst.name or not inst.matches_pattern(pattern):
                continue

            if pythons and inst.py not in pythons:
                continue

            if not inst.match_venv_pattern(venv_pattern):
                continue
            pkgs_str = inst.full_pkg_str
            env_str = env_to_str(inst.env)
            if interpreters or hash_only:
                python_interpreters.add(inst.py._hint)
                venv_hashes.add(inst.short_hash)
                continue

            if pipe_mode:
                print(
                    f"[#{n}]  {inst.short_hash}  {inst.name:12} {env_str} {inst.py} Packages({pkgs_str})"
                )
            else:
                table.add_row(
                    f"[cyan]#{n}[/cyan]",
                    f"[bold cyan]{inst.short_hash}[/bold cyan]",
                    f"[bold]{inst.name}[/bold]",
                    Pretty(inst.py),
                    env_str or "--",
                    f"[italic]{pkgs_str}[/italic]",
                )

        if table:
            rich_print(table)

        elif hash_only and venv_hashes:
            print("\n".join(sorted(venv_hashes)))

        elif interpreters and python_interpreters:
            print("\n".join(sorted(python_interpreters, key=Version)))

    def generate_base_venvs(
        self,
        pattern: t.Pattern[str],
        recreate: bool,
        skip_deps: bool,
        pythons: t.Optional[t.Set[Interpreter]],
    ) -> None:
        """Generate all the required base venvs."""
        # Find all the python interpreters used.
        required_pys: t.Set[Interpreter] = set(
            [
                inst.py
                for inst in self.venv.instances()
                if inst.py is not None
                and (not inst.name or inst.matches_pattern(pattern))
            ]
        )
        # Apply Python filters.
        if pythons:
            required_pys = required_pys.intersection(pythons)

        logger.info(
            "Generating virtual environments for interpreters %s",
            ",".join(str(s) for s in required_pys),
        )

        for py in required_pys:
            try:
                # We check if the venv existed already. If it didn't, we know we
                # have to install the dev package. Otherwise we assume that it
                # already has the dev package installed.
                py.create_venv(recreate)
            except CmdFailure as e:
                logger.error("Failed to create virtual environment.\n%s", e.proc.stdout)
            except FileNotFoundError:
                logger.error("Python version '%s' not found.", py)
            else:
                if skip_deps:
                    logger.info("Skipping global deps install.")
                    continue

                # Install the dev package into the base venv.
                install_dev_pkg(py.venv_path, force=True)

    def _generate_shell_rcfile(self):
        with tempfile.NamedTemporaryFile() as rcfile:
            rcfile.write()
            rcfile.flush()

    def _venvs_matching_identifier(self, identifier):
        for n, inst in enumerate(self.venv.instances()):
            if identifier != f"#{n}" and not inst.long_hash.startswith(identifier):
                continue

            assert inst.py is not None, inst
            try:
                venv_path = inst.venv_path
            except FileNotFoundError:
                raise RuntimeError("%s not available" % inst.py)
            yield inst, venv_path

    def requirements(self, ident):
        for inst, _ in self._venvs_matching_identifier(ident):
            with Status("Producing requirements.txt"):
                _ = inst.requirements

    def shell(self, ident, pass_env):
        for inst, venv_path in self._venvs_matching_identifier(ident):
            logger.info("Launching shell inside venv instance %s", inst)
            logger.debug("Setting venv path to %s", venv_path)

            # Generate the environment for the instance.
            if pass_env:
                env = os.environ.copy()
                env.update(dict(inst.env))
            else:
                env = dict(inst.env)

            # Should we expect the venv to be ready?
            with Status("Preparing shell virtual environment"):
                inst.py.create_venv(False)
                inst.prepare(env)

            pythonpath = inst.pythonpath
            if pythonpath:
                env["PYTHONPATH"] = (
                    f"{pythonpath}:{env['PYTHONPATH']}"
                    if "PYTHONPATH" in env
                    else pythonpath
                )
            script_path = inst.scriptpath
            if script_path:
                env["PATH"] = ":".join(
                    (script_path, env.get("PATH", os.environ["PATH"]))
                )

            with nspkgs(inst):
                with tempfile.NamedTemporaryFile() as rcfile:
                    rcfile.write(
                        SHELL_RCFILE.format(
                            venv_path=venv_path, name=inst.name
                        ).encode()
                    )
                    rcfile.flush()

                    try:
                        w, h = os.get_terminal_size()
                    except OSError:
                        w, h = 80, 24
                    c = pexpect.spawn(SHELL, ["-i"], dimensions=(h, w), env=env)
                    c.setecho(False)
                    c.sendline(f"source {rcfile.name}")
                    try:
                        c.interact()
                    except Exception:
                        pass
                    c.close()
                    sys.exit(c.exitstatus)

        else:
            logger.error(
                "No venv instance found for %s. Use 'riot list' to get a list of valid numbers.",
                ident,
            )

    @classmethod
    def run_cmd_venv(
        cls,
        venv: str,
        args: str,
        stdout: _T_stdio = subprocess.PIPE,
        executable: t.Optional[str] = None,
        env: t.Optional[t.Dict[str, str]] = None,
    ) -> _T_CompletedProcess:
        env = {} if env is None else env.copy()

        abs_venv = os.path.abspath(venv)
        env["VIRTUAL_ENV"] = abs_venv
        env["PATH"] = f"{abs_venv}/bin:" + env.get("PATH", "")

        try:
            # Ensure that we have the venv site-packages in the PYTHONPATH so
            # that the installed dev package depdendencies are available.
            sitepkgs_path = (
                next((Path(abs_venv) / "lib").glob("python*")) / "site-packages"
            )
            pythonpath = env.get("PYTHONPATH", None)
            env["PYTHONPATH"] = (
                os.pathsep.join((pythonpath, str(sitepkgs_path)))
                if pythonpath is not None
                else str(sitepkgs_path)
            )
        except StopIteration:
            pass

        for k in cls.ALWAYS_PASS_ENV:
            if k in os.environ and k not in env:
                env[k] = os.environ[k]

        env_str = " ".join(f"{k}={v}" for k, v in env.items())

        logger.debug("Executing command '%s' with environment '%s'", args, env_str)
        return run_cmd(args, stdout=stdout, executable=executable, env=env, shell=True)


def rmchars(chars: str, s: str) -> str:
    """Remove chars from s.

    >>> rmchars("123", "123456")
    '456'
    >>> rmchars(">=<.", ">=2.0")
    '20'
    >>> rmchars(">=<.", "")
    ''
    """
    for c in chars:
        s = s.replace(c, "")
    return s


def get_pep_dep(libname: str, version: str) -> str:
    """Return a valid PEP 508 dependency string.

    ref: https://www.python.org/dev/peps/pep-0508/

    >>> get_pep_dep("riot", "==0.2.0")
    'riot==0.2.0'
    """
    return f"{libname}{version}"


def env_to_str(envs: t.Dict[str, str]) -> str:
    """Return a human-friendly representation of environment variables.

    >>> env_to_str({"FOO": "BAR"})
    'FOO=BAR'
    >>> env_to_str({"K": "V", "K2": "V2"})
    'K=V K2=V2'
    """
    return " ".join(f"{k}={v}" for k, v in envs.items())


def run_cmd(
    args: t.Union[str, t.Sequence[str]],
    shell: bool = False,
    stdout: _T_stdio = subprocess.PIPE,
    executable: t.Optional[str] = None,
    env: t.Optional[t.Dict[str, str]] = None,
) -> _T_CompletedProcess:
    if shell:
        executable = SHELL

    logger.debug("Running command %s", args)
    r = subprocess.run(
        args,
        encoding=ENCODING,
        stdout=stdout,
        executable=executable,
        shell=shell,
        env=env,
    )
    logger.debug(r.stdout)

    if r.returncode != 0:
        raise CmdFailure("Command %s failed with code %s." % (args[0], r.returncode), r)
    return r


def expand_specs(specs: t.Dict[_K, t.List[_V]]) -> t.Iterator[t.Tuple[t.Tuple[_K, _V]]]:
    """Return the product of all items from the passed dictionary.

    In summary:

    {X: [X0, X1, ...], Y: [Y0, Y1, ...]} ->
      [(X, X0), (Y, Y0)), ((X, X0), (Y, Y1)), ((X, X1), (Y, Y0)), ((X, X1), (Y, Y1)]

    >>> list(expand_specs({"x": ["x0", "x1"]}))
    [(('x', 'x0'),), (('x', 'x1'),)]
    >>> list(expand_specs({"x": ["x0", "x1"], "y": ["y0", "y1"]}))
    [(('x', 'x0'), ('y', 'y0')), (('x', 'x0'), ('y', 'y1')), (('x', 'x1'), ('y', 'y0')), (('x', 'x1'), ('y', 'y1'))]
    """
    all_vals = [[(name, val) for val in vals] for name, vals in specs.items()]

    # Need to cast because the * star typeshed of itertools.product returns Any
    return t.cast(t.Iterator[t.Tuple[t.Tuple[_K, _V]]], itertools.product(*all_vals))


def pip_deps(pkgs: t.Dict[str, str]) -> str:
    return " ".join(
        [
            f"'{get_pep_dep(lib, version)}'"
            for lib, version in pkgs.items()
            if version is not None
        ]
    )


def install_dev_pkg(venv_path: str, force: bool = False) -> None:
    dev_pkg_lockfile = Path(venv_path) / ".riot-dev-pkg-installed"
    if dev_pkg_lockfile.exists() and not force:
        logger.info("Dev package already installed. Skipping.")
        return

    for setup_file in {"setup.py", "pyproject.toml"}:
        if Path(setup_file).exists():
            break
    else:
        logger.warning("No Python setup file found. Skipping dev package installation.")
        return

    logger.info("Installing dev package (edit mode) in %s.", venv_path)
    try:
        Session.run_cmd_venv(
            venv_path,
            "pip --disable-pip-version-check install -e .",
            env=dict(os.environ),
        )
        dev_pkg_lockfile.touch()
    except CmdFailure as e:
        logger.error("Dev install failed, aborting!\n%s", e.proc.stdout)
        sys.exit(1)
