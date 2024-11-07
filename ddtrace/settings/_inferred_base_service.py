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

CACHE: Dict[Tuple[str, ...], Optional[str]] = {}


class ServiceMetadata:
    def __init__(self, name: str):
        # prevent service name from being an empty string
        self.name = name if name != "" else None


class PythonDetector:
    def __init__(self, environ: Dict[str, str]):
        self.environ = environ
        self.name = "python"

        # This pattern matches:
        # - Starts with an optional directory (anything before the last '/' or '')
        # - Ends with the expected command name, possibly followed by a version
        # - Ensures that it does not end with .py
        # - Match /python, /python3.7, etc.
        self.pattern = r"(^|/)(?!.*\.py$)(" + re.escape("python") + r"(\d+\.\d+)?$)"

    def detect(self, args: List[str]) -> Optional[ServiceMetadata]:
        """
        Detects and returns service metadata based on the provided list of arguments.

        This function iterates through the provided arguments, skipping any that are
        flags (starting with '-') or environment variable assignments (containing '=').
        If a valid module flag ('-m') is encountered, it will switch to module detection mode.
        It checks for existing directories and deduces package names from provided paths
        to generate service metadata.

        Args:
            args (List[str]): A list of command-line arguments.

        Returns:
            Optional[ServiceMetadata]:
                - ServiceMetadata: The detected service metadata if found.
        """
        prev_arg_is_flag = False
        module_flag = False

        for arg in args:
            has_flag_prefix = arg.startswith("-")
            is_env_variable = "=" in arg

            should_skip_arg = prev_arg_is_flag or has_flag_prefix or is_env_variable

            if module_flag:
                return ServiceMetadata(arg)

            if not should_skip_arg:
                abs_path = pathlib.Path(arg).resolve()
                if not abs_path.exists():
                    continue
                stripped = abs_path
                if not stripped.is_dir():
                    stripped = stripped.parent
                value, ok = self.deduce_package_name(stripped)
                if ok:
                    return ServiceMetadata(value)
                return ServiceMetadata(self.find_nearest_top_level(stripped))

            if has_flag_prefix and arg == "-m":
                module_flag = True

            prev_arg_is_flag = has_flag_prefix

        return None

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

    def matches(self, command: str) -> bool:
        # Returns if the command matches the regex pattern for finding python executables / commands.
        return bool(re.search(self.pattern, command))


def detect_service(args: List[str]) -> Optional[str]:
    """
    Detects and returns the name of a service based on the provided list of command-line arguments.

    This function checks the provided arguments against a list of detector classes to identify
    the service type. If any of the arguments represent executables, they are ignored. The
    function iterates through the detector instances, applying their detection logic to the qualifying
    arguments in order to determine a service name.

    Args:
        args (List[str]): A list of command-line arguments.
        detector_classes (List[Type[Detector]]): A list of detector classes to use for service detection.
            Defaults to [PythonDetector].

    Returns:
        Optional[str]: The name of the detected service, or None if no service was detected.
    """
    detector_classes = [PythonDetector]

    if not args:
        return None

    cache_key = tuple(sorted(args))
    if cache_key in CACHE:
        return CACHE.get(cache_key)

    # Check both the included command args as well as the executable being run
    possible_commands = [*args, sys.executable]
    executable_args = set()

    # List of detectors to try in order
    detectors = {}
    for detector_class in detector_classes:
        detector_instance = detector_class(dict(os.environ))

        for i, command in enumerate(possible_commands):
            detector_name = detector_instance.name

            if detector_instance.matches(command):
                detectors.update({detector_name: detector_instance})
                # append to a list of arg indexes to ignore since they are executables
                executable_args.add(i)
                continue
            elif _is_executable(command):
                # append to a list of arg indexes to ignore since they are executables
                executable_args.add(i)

    args_to_search = []
    for i, arg in enumerate(args):
        # skip any executable args
        if i not in executable_args:
            args_to_search.append(arg)

    # Iterate through the matched detectors
    for detector in detectors.values():
        metadata = detector.detect(args_to_search)
        if metadata and metadata.name:
            CACHE[cache_key] = metadata.name
            return metadata.name
    CACHE[cache_key] = None
    return None


def _is_executable(file_path: str) -> bool:
    normalized_path = os.path.normpath(file_path)
    if not os.path.isfile(normalized_path):
        return False

    # Split the path into directories and check for 'bin'
    directory = os.path.dirname(normalized_path)
    while directory and os.path.dirname(directory) != directory:  # Check to prevent infinite loops
        if os.path.basename(directory).endswith("bin"):
            return True
        directory = os.path.dirname(directory)

    return False
