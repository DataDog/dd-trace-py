import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import typing as t


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / ".ext_cache"


def invoke_ext_hashes() -> tuple[list[tuple[str, str, str]], list[tuple[str, str, Path]]]:
    """Run ``setup.py ext_hashes`` and parse both output line types.

    Returns ``(ext_entries, dep_entries)`` where:

    - *ext_entries* — list of ``(name, hash, target_path)`` from ``#EXTHASH:`` lines
    - *dep_entries* — list of ``(name, config_hash, install_dir)`` from ``#SHAREDEPINFO:`` lines
    """
    # Older setuptools (Python ≤3.10) installs setup_requires into .eggs/ each
    # time setup.py is invoked.  If .eggs/ is left from a prior call the rename
    # of dist-info → EGG-INFO fails with ENOTEMPTY.  Remove it before each
    # invocation so every call starts with a clean slate.
    eggs_dir = ROOT / ".eggs"
    if eggs_dir.exists():
        shutil.rmtree(eggs_dir)

    output = subprocess.check_output([sys.executable, ROOT / "setup.py", "ext_hashes", "--inplace"])
    ext_entries: list[tuple[str, str, str]] = []
    dep_entries: list[tuple[str, str, Path]] = []
    for line in output.decode().splitlines():
        if line.startswith("#EXTHASH:"):
            ext_name, ext_hash, ext_target = t.cast(tuple[str, str, str], eval(line.split(":", 1)[-1].strip()))
            ext_entries.append((ext_name, ext_hash, ext_target))
        elif line.startswith("#SHAREDEPINFO:"):
            name, config_hash, install_path = t.cast(tuple[str, str, str], eval(line.split(":", 1)[-1].strip()))
            dep_entries.append((name, config_hash, Path(install_path)))
    return ext_entries, dep_entries


# ---------------------------------------------------------------------------
# Extension (.so) cache
# ---------------------------------------------------------------------------


def try_restore_from_cache() -> None:
    ext_entries, dep_entries = invoke_ext_hashes()

    for ext_name, ext_hash, ext_target in ext_entries:
        target = Path(ext_target)
        cache_dir = CACHE / ext_name / ext_hash
        target_dir = target.parent.resolve()
        if not (matches := list(cache_dir.glob(target.name))):
            print(f"Warning: No cached files found for {target.name} in {cache_dir}", file=sys.stderr)
            continue
        for d in matches:
            if d.is_file():
                print(f"Restoring {d.resolve()} to {target_dir.resolve()} directory")
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy(d.resolve(), target_dir.resolve())
                if (target_dir / d.name).exists():
                    print(f"Successfully copied {d.name} to {target_dir.resolve()} directory")
                else:
                    print(f"Failed to copy {d.name} to {target_dir.resolve()} directory")

    _restore_shared_deps(dep_entries)


def save_to_cache() -> None:
    ext_entries, dep_entries = invoke_ext_hashes()

    for ext_name, ext_hash, ext_target in ext_entries:
        target = Path(ext_target)
        cache_dir = CACHE / ext_name / ext_hash
        target_dir = target.parent.resolve()
        if not (matches := list(target_dir.glob(target.name))):
            print(f"Warning: No target files found for {target.name} in {target_dir}", file=sys.stderr)
            continue
        for f in matches:
            if f.is_file():
                print(f"Saving {f.resolve()} to {cache_dir.resolve()} directory")
                cache_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy(f.resolve(), cache_dir.resolve())
                if (cache_dir / f.name).exists():
                    print(f"Successfully copied {f.name} to {cache_dir.resolve()} directory")
                else:
                    print(f"Failed to copy {f.name} to {cache_dir.resolve()} directory")

    _save_shared_deps(dep_entries)


# ---------------------------------------------------------------------------
# Shared C++ dependency cache
#
# Dependency metadata comes entirely from setup.py via the #SHAREDEPINFO:
# lines emitted by the ext_hashes command.  No dependency-specific knowledge
# lives here; adding a new shared dep only requires updating setup.py.
# ---------------------------------------------------------------------------


def _restore_shared_deps(dep_entries: list[tuple[str, str, Path]]) -> None:
    """Restore shared dependency install trees from the ext cache.

    Each restored tree includes the sentinel file written by
    ``SharedDep.mark_built()``, so a subsequent build will skip recompilation.
    """
    for name, config_hash, install_dir in dep_entries:
        cache_dir = CACHE / "shared_deps" / name / config_hash
        if not cache_dir.exists():
            print(f"Warning: No cached {name} found (key={config_hash})", file=sys.stderr)
            continue
        if install_dir.exists():
            shutil.rmtree(install_dir)
        install_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(cache_dir, install_dir)
        print(f"Restored {name} from {cache_dir} to {install_dir}")


def _save_shared_deps(dep_entries: list[tuple[str, str, Path]]) -> None:
    """Save shared dependency install trees to the ext cache.

    Only the installed prefix (headers, static libs, CMake config) is cached,
    not the build directory, so entries are small and portable.
    """
    for name, config_hash, install_dir in dep_entries:
        if not install_dir.exists():
            print(f"Warning: {name} install dir {install_dir} not found, skipping", file=sys.stderr)
            continue
        cache_dir = CACHE / "shared_deps" / name / config_hash
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(install_dir, cache_dir)
        print(f"Saved {name} to {cache_dir}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args():
    parser = argparse.ArgumentParser(description="Cache and restore compiled extensions and shared C++ dependencies.")
    parser.add_argument(
        "--root",
        metavar="DIR",
        default=None,
        help=(
            "Override the cache root directory (default: <repo>/.ext_cache). "
            "Useful for local experiments, e.g. --root /tmp/ext_cache."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")
    subparsers.add_parser("restore", help="Restore extensions and shared deps from cache")
    subparsers.add_parser("save", help="Save extensions and shared deps to cache")
    return parser.parse_args()


def main():
    global CACHE

    args = parse_args()
    if args.root is not None:
        CACHE = Path(args.root)
        print(f"Using cache root: {CACHE}")

    if args.command == "restore":
        try_restore_from_cache()
    elif args.command == "save":
        save_to_cache()


if __name__ == "__main__":
    main()
