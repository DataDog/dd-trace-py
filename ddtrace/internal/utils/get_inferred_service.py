import ast
from importlib.util import find_spec
import os
from pathlib import Path
import re

import toml  # type: ignore

from ddtrace.vendor import psutil


pattern = r"[\"':;,]"


def _path_to_module(path):
    # normalize path, split off .py extension, and replace '/' with '.'
    path = os.path.normpath(path)
    module_path = os.path.splitext(path)[0]
    module_path = module_path.replace(os.sep, ".")
    return module_path


def _get_entrypoint_path_and_module():
    current_process = psutil.Process(os.getpid())
    cmdline = current_process.cmdline()
    module_name = None
    module_path = None

    if "-m" in cmdline:
        index = cmdline.index("-m")
        if index + 1 < len(cmdline):
            potential_module_name = cmdline[index + 1]
            spec = find_spec(potential_module_name)
            if spec is not None:
                module_name = potential_module_name
                module_path = spec.origin
            else:
                print(f"Error: '{potential_module_name}' is not a valid Python module")
    else:
        for i, arg in enumerate(cmdline):
            if "python" in arg.lower():
                # Loop through the remaining args to check if they are existing files
                for potential_path_str in cmdline[i + 1 :]:
                    potential_path = Path(potential_path_str).resolve()
                    if potential_path.exists():
                        module_name = _path_to_module(potential_path_str)
                        module_path = str(potential_path)
                        break
                break

    return module_path, module_name


def _search_files(file_names, start_path):
    current_path = Path(start_path).resolve()

    for parent in [current_path] + list(current_path.parents):
        for file_name in file_names:
            if (parent / file_name).exists():
                return parent / file_name
    return None


def _extract_setup_py_service_name(setup_file):
    breakpoint()
    with open(setup_file, "r") as f:
        tree = ast.parse(f.read(), filename=setup_file)

    class SetupVisitor(ast.NodeVisitor):
        def __init__(self):
            self.package_name = None

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == "setup":
                for keyword in node.keywords:
                    if keyword.arg == "name":
                        if isinstance(keyword.value, ast.Constant):
                            self.package_name = keyword.value.value
            self.generic_visit(node)

    visitor = SetupVisitor()
    visitor.visit(tree)
    return visitor.package_name


def _find_package_name(start_path):
    files = ["setup.py", "pyproject.toml"]
    breakpoint()
    pkg_metadata = _search_files(files, start_path)
    if pkg_metadata:
        if getattr(pkg_metadata, "name", "") == files[0]:
            package_name = _extract_setup_py_service_name(pkg_metadata)
            if package_name:
                return re.sub(pattern, "", package_name)
        elif getattr(pkg_metadata, "name", "") == files[1]:
            # Parse pyproject.toml file to get package name
            with open(pkg_metadata, "r") as f:
                pyproject_data = toml.load(f)
                if "project" in pyproject_data and "name" in pyproject_data["project"]:
                    return re.sub(pattern, "", pyproject_data["project"].get("name"))

                if "tool" in pyproject_data and "poetry" in pyproject_data["tool"]:
                    return re.sub(pattern, "", pyproject_data["tool"]["poetry"].get("name"))


def get_inferred_service(default):
    inferred_path, inferred_module = _get_entrypoint_path_and_module()
    inferred_pkg = _find_package_name(inferred_path if inferred_path else os.getcwd())
    if inferred_pkg:
        return inferred_pkg
    elif inferred_module:
        return inferred_module
    return default
