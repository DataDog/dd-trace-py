import collections
import glob
import os
from pathlib import Path
import shutil
import subprocess
import sys

if len(sys.argv) != 2:
    print("Usage: python dedupe.py <source dir>")
    sys.exit(1)

source_dir = Path(sys.argv[1])

# Find all duplicate files
print("Finding duplicate files")
file_hashes = collections.defaultdict(set)
for src in glob.glob(f"{source_dir}/**/*", recursive=True):
    if not os.path.isfile(src):
        continue
    res = subprocess.check_output(["sha256sum", str(src)])
    file_hash, _, _ = res.decode().partition(" ")
    file_hashes[file_hash].add(Path(src))


# Replace shared files with soft links
shared_dir = source_dir / "shared"
try:
    shutil.rmtree(shared_dir)
except Exception:
    pass
os.makedirs(shared_dir)

for file_hash in file_hashes:
    # Skip unique files that aren't duplicates
    if len(file_hashes[file_hash]) <= 1:
        continue

    src = next(iter(file_hashes[file_hash]))
    basename = os.path.basename(src)
    dest = shared_dir / f"{file_hash}_{basename}"
    shutil.copy(src, dest)

    for src in file_hashes[file_hash]:
        dest_rel = dest.relative_to(source_dir)
        src_rel = Path(*([".." for _ in src.relative_to(source_dir).parts[:-1]] + [dest_rel]))
        print(f"Replacing {src} with symlink to {src_rel}")
        os.remove(src)
        os.symlink(src_rel, src)
