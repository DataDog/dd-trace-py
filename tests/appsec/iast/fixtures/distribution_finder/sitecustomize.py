from importlib import import_module
from importlib.machinery import PathFinder
from importlib.machinery import SourceFileLoader
import sys


class Loader(SourceFileLoader):
    def exec_module(self, module):
        module.loader_ran = True
        super().exec_module(module)


class Finder:
    def find_spec(self, fullname, path, target=None):
        if fullname == "finder_dependency":
            spec = PathFinder.find_spec(fullname, path, target)
            spec.loader = Loader(fullname, spec.origin)
            return spec
        return None

    def find_distributions(self, context):
        for name in ("finder_helper", "finder_allowed", "finder_dependency"):
            import_module(name)
        return ()


# PYTHONPATH is visible to both ddtrace-run and its child. Install this finder
# only in the child, when ddtrace imports the user's sitecustomize.
if "ddtrace.bootstrap.preload" in sys.modules:
    sys.meta_path.insert(sys.meta_path.index(PathFinder), Finder())
