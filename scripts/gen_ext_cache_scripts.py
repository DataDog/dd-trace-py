import argparse
from pathlib import Path
import subprocess
import sys
import typing as t


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / ".ext_cache"
RESTORE_FILE = HERE / "restore-ext-cache.sh"
SAVE_FILE = HERE / "save-ext-cache.sh"


def invoke_ext_hashes():
    output = subprocess.check_output([sys.executable, ROOT / "setup.py", "ext_hashes", "--inplace"])
    results = []
    for line in output.decode().splitlines():
        if not line.startswith("#EXTHASH:"):
            continue
        ext_name, ext_hash, ext_target = t.cast(t.Tuple[str, str, str], eval(line.split(":", 1)[-1].strip()))
        results.append((ext_name, ext_hash, ext_target))
    return results


def gen_restore():
    # Generate the hashes of the extensions
    results = invoke_ext_hashes()
    cached_files = set()
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
                cached_files.add((str(d.resolve()), str(target_dir / d.name)))

    # Generate the restore script
    RESTORE_FILE.write_text(
        "\n".join(
            [
                f"    test -f {cached_file} && (cp {cached_file} {dest} && touch {dest} "
                f"&& echo 'Restored {cached_file} -> {dest}') || true"
                for cached_file, dest in cached_files
            ]
        )
    )


def gen_save():
    # Generate the hashes of extensions
    results = invoke_ext_hashes()
    files_to_cache = set()
    for ext_name, ext_hash, ext_target in results:
        target = Path(ext_target)
        cache_dir = CACHE / ext_name / ext_hash
        target_dir = target.parent.resolve()
        # Check if the target file exist in the target directory
        if not (matches := list(target_dir.glob(target.name))):
            print(f"Warning: No target files found for {target.name} in {target_dir}", file=sys.stderr)
            continue
        # if we found the target file, add it to the files_to_cache set
        for d in matches:
            if d.is_file():
                files_to_cache.add((str(d.resolve()), str(cache_dir / d.name)))

    # Generate the save script
    SAVE_FILE.write_text(
        "\n".join(
            [
                f"    test -f {file_to_cache} && mkdir -p {Path(dest).parent} && (cp {file_to_cache} {dest} "
                f"&& echo 'Saved {file_to_cache} -> {dest}' || true)"
                for file_to_cache, dest in files_to_cache
            ]
        )
    )


def parse_args():
    args = argparse.ArgumentParser()
    args.add_argument("--gen_restore", action="store_true")
    args.add_argument("--gen_save", action="store_true")
    return args.parse_args()


def main():
    args = parse_args()
    if args.gen_restore:
        gen_restore()
    elif args.gen_save:
        gen_save()


if __name__ == "__main__":
    main()
