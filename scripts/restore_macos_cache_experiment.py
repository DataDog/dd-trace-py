from pathlib import Path
import shutil
import sys

import ext_cache
from extract_debug_symbols import dsym_matches


def damage_symbols(symbols_dir: Path) -> None:
    internal = symbols_dir / "ddtrace" / "internal"
    (encoding_bundle,) = internal.glob("_encoding.*.dSYM")
    (threads_bundle,) = internal.glob("_threads.*.dSYM")
    (encoding_dwarf,) = (encoding_bundle / "Contents" / "Resources" / "DWARF").iterdir()
    (threads_dwarf,) = (threads_bundle / "Contents" / "Resources" / "DWARF").iterdir()
    shutil.copyfile(encoding_dwarf, threads_dwarf)
    shutil.rmtree(encoding_bundle)


def main() -> None:
    symbols_dir = Path(sys.argv[1])
    if sys.argv[2] == "invalidate":
        damage_symbols(symbols_dir)

    def accept_file(cached_file: Path, target: Path) -> bool:
        if target.suffix not in {".so", ".dylib"}:
            return True
        _, *wheel_parts = target.relative_to(ext_cache.ROOT / "build").parts
        wheel_path = Path(*wheel_parts)
        bundle = symbols_dir / wheel_path.with_suffix(".dSYM")
        if not bundle.is_dir():
            print(f"Missing dSYM for {wheel_path}; rebuilding")
            return False
        if not dsym_matches(cached_file, bundle):
            print(f"dSYM UUID mismatch for {wheel_path}; rebuilding")
            return False
        return True

    ext_cache.INPLACE = False
    ext_cache.try_restore_from_cache(shared_deps=False, accept_file=accept_file)


if __name__ == "__main__":
    main()
