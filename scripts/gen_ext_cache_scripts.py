from pathlib import Path
import subprocess
import sys
import typing as t


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / ".ext_cache"

# Get extension information from setup.py
output = subprocess.check_output([sys.executable, ROOT / "setup.py", "ext_hashes", "--inplace"])
cached_files = []
for line in output.decode().splitlines():
    if not line.startswith("#EXTHASH:"):
        continue
    ext_name, ext_hash, ext_target = t.cast(t.Tuple[str, str, str], eval(line.split(":", 1)[-1].strip()))
    target = Path(ext_target)
    cache_dir = CACHE / ext_name / ext_hash
    if ext_target.endswith("*"):
        target_dir = target.parent.resolve()
        for d in cache_dir.glob(target.name):
            if d.is_file():
                cached_files.append((str(d.resolve()), str(target_dir / d.name)))
    else:
        cached_files.append((str(cache_dir / target.name), ext_target))

# Generate the restore script
(HERE / "restore-ext-cache.sh").write_text(
    "\n".join(
        [
            f"    test -f {cached_file} && (cp {cached_file} {dest} && touch {dest} "
            f"&& echo 'Restored {cached_file} -> {dest}') || true"
            for cached_file, dest in cached_files
        ]
    )
)

# Generate the save script
(HERE / "save-ext-cache.sh").write_text(
    "\n".join(
        [
            f"    test -f {cached_file} || mkdir -p {Path(cached_file).parent} && (cp {dest} {cached_file} "
            f"&& echo 'Saved {dest} -> {cached_file}' || true)"
            for cached_file, dest in cached_files
        ]
    )
)
