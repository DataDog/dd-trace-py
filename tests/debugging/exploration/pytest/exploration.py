from pathlib import Path
import sys


def pytest_configure(config):
    # Make the exploration module available for import
    sys.path.append(str(Path(__file__).parents[1]))
    import preload  # noqa: F401
