from pathlib import Path


def _get_relpath_str(rootpath: Path, path: Path):
    try:
        return str(path.relative_to(rootpath))
    except ValueError:
        return str(path)


def _get_relpath_dict(rootpath: str, dict_to_update: dict):
    """Expects a dictionary of path strings to anything and returns an identical dictionary with the keys changed to
    relative path strings
    """
    return {_get_relpath_str(Path(rootpath), Path(path)): lines for path, lines in dict_to_update.items()}
