from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Get extension information from setup.py
output = subprocess.check_output([sys.executable, ROOT / "setup.py", "ext_hashes", "--inplace"])
cached_files = []
for line in output.decode().splitlines():
    if not line.startswith("#EXTHASH:"):
        continue
    ext_name, ext_hash, ext_target = eval(line.split(":", 1)[-1].strip())
    cached_files.append((f".ext_cache/{ext_name}/{ext_hash}/{Path(ext_target).name}", ext_target))

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
