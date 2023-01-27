import argparse
import os
import pathlib


def call_slotscheck(filename: str) -> None:
    """Adjust the Python path and call slotscheck"""
    path = pathlib.Path(filename)
    partial_paths = list(reversed(path.parents))
    current_path = None
    for next_path in partial_paths:
        if not next_path.is_dir() or any(file.name == "__init__.py" for file in next_path.iterdir()):
            break
        current_path = next_path
    if current_path is None:
        os.system("python -m slotscheck -v {filename}")
    else:
        print("Adjusting the path to {current_path}")
        os.system("PYTHON_PATH={current_path} python -m slotscheck -v {filename}")


if __name__ == "main":
    parser = argparse.ArgumentParser(
        prog=__file__,
        description="Check one python with slotscheck by adjusting the adequate path",
    )
    parser.add_argument("filename")
    args = parser.parse_args()
    call_slotscheck(args.filename)
