from pathlib import Path
import subprocess
import sys
import typing as t


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / ".ext_cache"
RESTORE_FILE = HERE / "restore-ext-cache.sh"
SAVE_FILE = HERE / "save-ext-cache.sh"

# Get extension information from setup.py
output = subprocess.check_output([sys.executable, ROOT / "setup.py", "ext_hashes", "--inplace"])
cached_files: t.Set[t.Tuple[str, str]] = set()
for line in output.decode().splitlines():
    if not line.startswith("#EXTHASH:"):
        continue
    ext_name, ext_hash, ext_target = t.cast(t.Tuple[str, str, str], eval(line.split(":", 1)[-1].strip()))
    target = Path(ext_target)
    cache_dir = CACHE / ext_name / ext_hash
    if ext_target.endswith("*"):
        target_dir = target.parent.resolve()
        if RESTORE_FILE.exists():
            # Iterate over the target as these are the files we want to cache
            for d in target_dir.glob(target.name):
                if d.is_file():
                    cached_files.add((str(cache_dir / d.name), str(d.resolve())))
        else:
            # Iterate over the cached files as these are the ones we want to
            # restore
            for d in cache_dir.glob(target.name):
                if d.is_file():
                    cached_files.add((str(d.resolve()), str(target_dir / d.name)))
    else:
        cached_files.add((str(cache_dir / target.name), ext_target))

# Generate the restore script on the first run
if not RESTORE_FILE.exists():
    RESTORE_FILE.write_text(
        "\n".join(
            [
                f"    test -f {cached_file} && (cp {cached_file} {dest} && touch {dest} "
                f"&& echo '- restored {Path(dest).relative_to(ROOT)}') || true"
                for cached_file, dest in cached_files
            ]
        )
    )
else:
    # Generate the save script on the second run
    SAVE_FILE.write_text(
        "\n".join(
            [
                f"    test -f {cached_file} || mkdir -p {Path(cached_file).parent} && (cp {dest} {cached_file} "
                f"&& echo '- saved {Path(dest).relative_to(ROOT)}' || true)"
                for cached_file, dest in cached_files
            ]
        )
    )
