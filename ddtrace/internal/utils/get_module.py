import os
from pathlib import Path

import psutil
import toml  # type: ignore


def get_entrypoint_path():
    current_process = psutil.Process(os.getpid())
    cmdline = current_process.cmdline()
    module_name = None

    if "-m" in cmdline:
        index = cmdline.index("-m")
        module_name = cmdline[index + 1]
        print("Module used to start the application:", module_name)
    elif "python" in cmdline:
        index = cmdline.index("python")
        module_name = cmdline[index + 1]
        print("Module used to start the application:", module_name)
    return module_name


def search_files(file_names, start_path):
    current_path = Path(start_path).resolve()

    for parent in [current_path] + list(current_path.parents):
        for file_name in file_names:
            if (parent / file_name).exists():
                return parent / file_name
    return None


def find_package_name(start_path):
    files = ["setup.py", "pyproject.toml"]

    pkg = search_files(files, start_path)
    if pkg:
        if getattr(pkg, "name", "") == files[0]:
            with open(pkg, "r") as f:
                for line in f:
                    if "name=" in line:
                        package_name = line.split("=")[1].strip().strip("'\"")
                        return package_name
        elif getattr(pkg, "name", "") == files[1]:
            # Parse pyproject.toml file to get package name
            with open(pkg, "r") as f:
                pyproject_data = toml.load(f)
                if "project" in pyproject_data and "name" in pyproject_data["project"]:
                    return pyproject_data["project"].get("name")

                if "tool" in pyproject_data and "poetry" in pyproject_data["tool"]:
                    return pyproject_data["tool"]["poetry"].get("name")
