import os
import fnmatch
import pathlib

INIT_PY = "__init__.py"
ALL_PY_FILES = "*.py"
GUNICORN_CMD_ARGS = "GUNICORN_CMD_ARGS"
WSGI_APP_ENV = "WSGI_APP"


class DetectionContext:
    def __init__(self, envs):
        self.envs = envs


class ServiceMetadata:
    def __init__(self, name):
        self.name = name


class PythonDetector:
    def __init__(self, ctx):
        self.ctx = ctx

    def expected_command_name(self):
        return 'python'

    def detect(self, args):
        prev_arg_is_flag = False
        module_flag = False

        for arg in args:
            has_flag_prefix = arg.startswith("-")
            is_env_variable = '=' in arg

            should_skip_arg = prev_arg_is_flag or has_flag_prefix or is_env_variable

            if module_flag:
                return ServiceMetadata(arg), True

            if not should_skip_arg:
                wd = self.working_dir_from_envs(self.ctx.envs)
                abs_path = self.abs_path(arg, wd)
                if not pathlib.Path(abs_path).exists():
                    return ServiceMetadata(""), False  # File not found
                stripped = abs_path
                if not pathlib.Path(stripped).is_dir():
                    stripped = str(pathlib.Path(stripped).parent)
                value, ok = self.deduce_package_name(stripped)
                if ok:
                    return ServiceMetadata(value), True
                return ServiceMetadata(self.find_nearest_top_level(stripped)), True

            if has_flag_prefix and arg == "-m":
                module_flag = True

            prev_arg_is_flag = has_flag_prefix

        return ServiceMetadata(""), False

    def working_dir_from_envs(self, envs):
        # Assuming the function extracts working directory from envs
        return os.getcwd()

    def abs_path(self, a, wd):
        # Converts the given path to an absolute path
        return str(pathlib.Path(wd) / a)

    # deduce_package_name is walking until a `__init__.py` is not found.
    # All the dir traversed are joined then with `.` 
    def deduce_package_name(self, fp):
        up = str(pathlib.Path(fp).parent)
        current = fp
        traversed = []

        while current != up:
            if not pathlib.Path(current, INIT_PY).exists():
                break
            traversed.insert(0, pathlib.Path(current).name)
            current = up
            up = str(pathlib.Path(current).parent)

        return ".".join(traversed), len(traversed) > 0

    # findNearestTopLevel returns the top level dir containing a .py file starting walking up from fp
    def find_nearest_top_level(self, fp):
        up = str(pathlib.Path(fp).parent)
        current = fp
        last = current

        while current != up:
            if not fnmatch.filter(os.listdir(current), ALL_PY_FILES):
                break
            last = current
            current = up
            up = str(pathlib.Path(current).parent)

        return pathlib.Path(last).name


class GunicornDetector:
    def __init__(self, ctx):
        self.ctx = ctx

    def expected_command_name(self):
        return 'gunicorn'

    def detect(self, args):
        # breakpoint()
        from_env = self.extract_env_var(GUNICORN_CMD_ARGS)
        if from_env:
            name, ok = self.extract_gunicorn_name_from(from_env.split())
            if ok:
                return ServiceMetadata(name), True

        wsgi_app = self.extract_env_var(WSGI_APP_ENV)
        if wsgi_app:
            return ServiceMetadata(self.parse_name_from_wsgi_app(wsgi_app)), True

        name, ok = self.extract_gunicorn_name_from(args)
        if ok:
            return ServiceMetadata(name), True

        return ServiceMetadata("gunicorn"), True

    def extract_env_var(self, var_name):
        return os.getenv(var_name)

    def extract_gunicorn_name_from(self, args):
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
                skip = '=' not in arg
                if skip:
                    continue
                if arg.startswith("--name="):
                    return arg[len("--name="):], True
            else:
                return self.parse_name_from_wsgi_app(args[-1]), True
        return "", False

    def parse_name_from_wsgi_app(self, wsgi_app):
        name, _, _ = wsgi_app.partition(":")
        return name


def detect_service(args, detector_classes = [PythonDetector, GunicornDetector]):
    ctx = DetectionContext(os.environ)

    # Check if args is not empty
    if not args:
        return None

    # Trim the first argument to ensure we're checking the correct command
    command = args[0].strip()

    # List of detectors to try in order
    detectors = []
    for detector_class in detector_classes:
        detector_instance = detector_class(ctx)

        # Check if the command matches the detector's expected executable name.
        if command.startswith(detector_instance.expected_command_name()):
            detectors.append(detector_instance)

    # Iterate through the matched detectors
    for detector in detectors:
        # Pass the trimmed args (excluding the detected command)
        metadata, detected = detector.detect(args[1:])  # Pass args[1:] to skip the command itself
        if detected and metadata.name:
            return metadata.name
    return None
