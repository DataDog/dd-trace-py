import argparse
import logging
import os
import pathlib
import sys


def call_slotscheck(filenames: list[str]) -> None:
    """Adjust the Python path and call slotscheck"""
    all_paths = []
    for filename in filenames:
        path = pathlib.Path(filename)
        partial_paths = list(reversed(path.parents))
        current_path = None
        for next_path in partial_paths:
            if not next_path.is_dir() or any(file.name == "__init__.py" for file in next_path.iterdir()):
                break
            current_path = next_path
        if current_path is not None:
            all_paths.append(current_path)
    if all_paths:
        python_path = ":".join(map(str, all_paths))
        if "PYTHONPATH" in os.environ:
            python_path = python_path + ":" + os.environ["PYTHONPATH"]
        logging.Logger(__name__).warning("PYTHONPATH='%s' for %s", python_path, sys.executable)
        os.execvpe(
            sys.executable,
            [sys.executable, "-m", "slotscheck", "-v"] + filenames,
            os.environ | {"PYTHONPATH": python_path},
        )
    else:
        os.execvp(sys.executable, [sys.executable, "-m", "slotscheck", "-v"] + filenames)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog=__file__,
        description="Check one python with slotscheck by adjusting the adequate path",
    )
    print(sys.argv[1:])
    parser.add_argument("filenames", type=str, nargs="*")
    args = parser.parse_args()
    if args.filenames:
        call_slotscheck(args.filenames)
