"""Main command pyddprofile."""
import os
import sys


def main():
    bootstrap_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "bootstrap"))

    python_path = os.environ.get("PYTHONPATH")

    if python_path:
        os.environ["PYTHONPATH"] = "%s%s%s" % (bootstrap_dir, os.path.pathsep, python_path)
    else:
        os.environ["PYTHONPATH"] = bootstrap_dir

    os.execl(sys.executable, sys.executable, *sys.argv[1:])


if __name__ == "__main__":
    main()
