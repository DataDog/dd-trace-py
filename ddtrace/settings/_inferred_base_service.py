import fnmatch
import os
import pathlib
import re
import sys
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple


INIT_PY = "__init__.py"
ALL_PY_FILES = "*.py"
GUNICORN_CMD_ARGS = "GUNICORN_CMD_ARGS"
WSGI_APP_ENV = "WSGI_APP"


CACHE: Dict[str, Optional[str]] = {}


class DetectionContext:
    def __init__(self, envs):
        self.envs = envs


class ServiceMetadata:
    def __init__(self, name):
        self.name = name


class PythonDetector:
    def __init__(self, ctx):
        self.ctx = ctx
        name = "python"
        self.name = name

        # This pattern matches:
        # - Starts with an optional directory (anything before the last '/' or '')
        # - Ends with the expected command name, possibly followed by a version
        # - Ensures that it does not end with .py
        # - Match /python, /python3.7, etc.
        self.pattern = r"(^|/)(?!.*\.py$)(" + re.escape(name) + r"(\d+\.\d+)?$)"

    def detect(self, args: List[str]) -> Tuple[ServiceMetadata, bool]:
        prev_arg_is_flag = False
        module_flag = False

        for arg in args:
            has_flag_prefix = arg.startswith("-")
            is_env_variable = "=" in arg

            should_skip_arg = prev_arg_is_flag or has_flag_prefix or is_env_variable

            if module_flag:
                return ServiceMetadata(arg), True

            if not should_skip_arg:
                abs_path = pathlib.Path(arg).resolve()
                if not abs_path.exists():
                    continue
                stripped = abs_path
                if not stripped.is_dir():
                    stripped = stripped.parent
                value, ok = self.deduce_package_name(stripped)
                if ok:
                    return ServiceMetadata(value), True
                return ServiceMetadata(self.find_nearest_top_level(stripped)), True

            if has_flag_prefix and arg == "-m":
                module_flag = True

            prev_arg_is_flag = has_flag_prefix

        return ServiceMetadata(""), False

    def deduce_package_name(self, fp: pathlib.Path) -> Tuple[str, bool]:
        # Walks the file path until a `__init__.py` is not found.
        # All the dir traversed are joined then with `.`
        up = pathlib.Path(fp).parent
        current = fp
        traversed: List[str] = []

        while current != up:
            if not (current / INIT_PY).exists():
                break
            traversed.insert(0, current.name)
            current = up
            up = current.parent

        return ".".join(traversed), len(traversed) > 0

    def find_nearest_top_level(self, fp: pathlib.Path) -> str:
        # returns the top level dir containing a .py file starting walking up from fp
        up = fp.parent
        current = fp
        last = current

        while current != up:
            if not fnmatch.filter(os.listdir(current), ALL_PY_FILES):
                break
            last = current
            current = up
            up = current.parent

        return last.name


class GunicornDetector:
    def __init__(self, ctx):
        self.ctx = ctx
        name = "gunicorn"
        self.name = name
        self.pattern = name

    def expected_command_name(self) -> str:
        return "gunicorn"

    def detect(self, args: List[str]) -> Tuple[ServiceMetadata, bool]:
        from_env = os.getenv(GUNICORN_CMD_ARGS)
        if from_env:
            name, ok = self.extract_gunicorn_name_from(from_env.split())
            if ok:
                return ServiceMetadata(name), True

        wsgi_app = os.getenv(WSGI_APP_ENV)
        if wsgi_app:
            return ServiceMetadata(self.parse_name_from_wsgi_app(wsgi_app)), True

        name, ok = self.extract_gunicorn_name_from(args)
        if ok:
            return ServiceMetadata(name), True

        return ServiceMetadata("gunicorn"), True

    def extract_gunicorn_name_from(self, args: List[str]) -> Tuple[str, bool]:
        skip = False
        capture = False

        for arg in args:
            if capture:
                return arg, True
            if skip:
                skip = False
                continue
            if arg.startswith("-"):
                if arg == "-n":
                    capture = True
                    continue
                skip = "=" not in arg
                if skip:
                    continue
                if arg.startswith("--name="):
                    return arg[len("--name=") :], True
            else:
                return self.parse_name_from_wsgi_app(args[-1]), True
        return "", False

    def parse_name_from_wsgi_app(self, wsgi_app: str) -> str:
        name, _, _ = wsgi_app.partition(":")
        return name


def detect_service(args: List[str], detector_classes=[PythonDetector, GunicornDetector]) -> Optional[str]:
    # Check if args is empty
    if not args:
        return None

    cache_key = ";".join(args)
    if cache_key in CACHE:
        return CACHE.get(cache_key)

    ctx = DetectionContext(os.environ)

    # Check both the included command args as well as the executable being run
    possible_commands = [*args, sys.executable]
    executable_args = set()

    # List of detectors to try in order
    detectors = {}
    for detector_class in detector_classes:
        detector_instance = detector_class(ctx)

        for i in range(len(possible_commands)):
            detector_name = detector_instance.name
            detector_pattern = detector_instance.pattern

            command = possible_commands[i]

            if re.search(detector_pattern, command):
                detectors.update({detector_name: detector_instance})
                # append to a list of arg indexes to ignore since they are executables
                executable_args.add(i)
                continue
            elif _is_executable(command):
                # append to a list of arg indexes to ignore since they are executables
                executable_args.add(i)

    args_to_search = []
    for i in range(len(args)):
        arg = args[i]
        # skip any executable args
        if i not in executable_args:
            args_to_search.append(arg)

    # Iterate through the matched detectors
    for detector in detectors.values():
        metadata, detected = detector.detect(args_to_search)
        if detected and metadata.name:
            CACHE[cache_key] = metadata.name
            return metadata.name
    CACHE[cache_key] = None
    return None


def _is_executable(file_path):
    normalized_path = os.path.normpath(file_path)
    if not os.path.isfile(normalized_path):
        return False

    # Split the path into directories and check for 'bin'
    directory = os.path.dirname(normalized_path)
    while directory and os.path.dirname(directory) != directory:  # Check to prevent infinite loops
        if os.path.basename(directory) == "bin":
            return True
        directory = os.path.dirname(directory)

    return False
