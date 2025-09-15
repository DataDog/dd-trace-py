import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import typing as t


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / ".ext_cache"


def invoke_ext_hashes():
    output = subprocess.check_output([sys.executable, ROOT / "setup.py", "ext_hashes", "--inplace"])
    results = []
    for line in output.decode().splitlines():
        if not line.startswith("#EXTHASH:"):
            continue
        ext_name, ext_hash, ext_target = t.cast(t.Tuple[str, str, str], eval(line.split(":", 1)[-1].strip()))
        results.append((ext_name, ext_hash, ext_target))
    return results


def try_restore_from_cache():
    # Generate the hashes of the extensions
    results = invoke_ext_hashes()
    for ext_name, ext_hash, ext_target in results:
        target = Path(ext_target)
        cache_dir = CACHE / ext_name / ext_hash
        target_dir = target.parent.resolve()
        # Check if the cached file exist in the cache directory
        if not (matches := list(cache_dir.glob(target.name))):
            print(f"Warning: No cached files found for {target.name} in {cache_dir}", file=sys.stderr)
            continue
        # if the cached file exist, add it to the cached_files set
        for d in matches:
            if d.is_file():
                print(f"Restoring {d.resolve()} to {target_dir.resolve()} directory")
                shutil.copy(d.resolve(), target_dir.resolve())
                # check that the file is indeed copied
                if (target_dir / d.name).exists():
                    print(f"Successfully copied {d.name} to {target_dir.resolve()} directory")
                else:
                    print(f"Failed to copy {d.name} to {target_dir.resolve()} directory")


def save_to_cache():
    # Generate the hashes of extensions
    results = invoke_ext_hashes()
    for ext_name, ext_hash, ext_target in results:
        target = Path(ext_target)
        cache_dir = CACHE / ext_name / ext_hash
        target_dir = target.parent.resolve()
        # Check if the target file exist in the target directory
        if not (matches := list(target_dir.glob(target.name))):
            print(f"Warning: No target files found for {target.name} in {target_dir}", file=sys.stderr)
            continue
        # if we found the target file, add it to the files_to_cache set
        for f in matches:
            if f.is_file():
                # So we'd need to save f to the cache directory
                print(f"Saving {f.resolve()} to {cache_dir.resolve()} directory")
                cache_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy(f.resolve(), cache_dir.resolve())
                # check that the file is indeed copied
                if (cache_dir / f.name).exists():
                    print(f"Successfully copied {f.name} to {cache_dir.resolve()} directory")
                else:
                    print(f"Failed to copy {f.name} to {cache_dir.resolve()} directory")


def parse_args():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")

    # Add restore command
    subparsers.add_parser("restore", help="Restore extensions from cache")

    # Add save command
    subparsers.add_parser("save", help="Save extensions to cache")

    return parser.parse_args()


def main():
    args = parse_args()
    if args.command == "restore":
        try_restore_from_cache()
    elif args.command == "save":
        save_to_cache()


if __name__ == "__main__":
    main()
