import csv
import os
import re

import toml


def requirements_to_csv():
    """
    Reads dependencies from pyproject.toml and writes them to a CSV file
    in the root directory and in the lib-injection/sources directory.
    """
    with open("pyproject.toml", "r") as f:
        data = toml.load(f)

    rows = [["Dependency", "Version Specifier", "Python Version"]]

    def process_deps(dependencies):
        for dep in dependencies:
            python_version_marker = ""
            if ";" in dep:
                parts = dep.split(";", 1)
                dep_without_marker = parts[0].strip()
                marker = parts[1].strip()
                if "python_version" in marker:
                    python_version_marker = marker
            else:
                dep_without_marker = dep.strip()

            match = re.match(r"([^~<>=!\[]+)(.*)", dep_without_marker)
            if match:
                name = match.group(1).strip()
                version = match.group(2).strip()
            else:
                name = dep_without_marker.strip()
                version = ""
            rows.append([name, version, python_version_marker])

    # Process main dependencies
    if "project" in data and "dependencies" in data["project"]:
        process_deps(data["project"]["dependencies"])

    # Process optional dependencies
    if "project" in data and "optional-dependencies" in data["project"]:
        for deps in data["project"]["optional-dependencies"].values():
            process_deps(deps)

    # Write to CSV files
    output_paths = ["requirements.csv", os.path.join("lib-injection", "sources", "requirements.csv")]

    for path in output_paths:
        if "lib-injection" in path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(rows)


if __name__ == "__main__":
    requirements_to_csv()
