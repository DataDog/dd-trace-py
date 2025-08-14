#!/usr/bin/env python3
"""
Extract debug symbols from wheels and create separate debug symbol packages.

This script:
1. Extracts debug symbols (.debug files on Linux, .dSYM bundles on macOS) from wheels
2. Creates separate debug symbol packages
3. Removes debug symbols from the original wheel
"""

import argparse
import csv
import fnmatch
import io
import os
from pathlib import Path
import sys
from typing import List
from typing import Tuple
import zipfile


def get_debug_symbol_patterns():
    """Get file patterns for debug symbols based on platform."""
    return ["*.debug", "*.dSYM/*"]


def find_debug_symbols_in_wheel(wheel_path: str) -> List[Tuple[str, bytes]]:
    """Find debug symbols in a wheel file."""
    debug_symbols = []
    patterns = get_debug_symbol_patterns()

    with zipfile.ZipFile(wheel_path, "r") as wheel:
        for file_info in wheel.infolist():
            if any(fnmatch.fnmatch(file_info.filename, pattern) for pattern in patterns):
                debug_symbols.append((file_info.filename, wheel.read(file_info.filename)))

    return debug_symbols


def create_debug_symbols_package(wheel_path: str, debug_symbols: List[Tuple[str, bytes]], output_dir: str):
    """Create a separate debug symbols package."""
    wheel_name = Path(wheel_path).stem
    debug_package_name = f"{wheel_name}-debug-symbols.zip"
    debug_package_path = os.path.join(output_dir, debug_package_name)

    with zipfile.ZipFile(debug_package_path, "w", zipfile.ZIP_DEFLATED) as debug_zip:
        for filename, content in debug_symbols:
            debug_zip.writestr(filename, content)

    print(f"Created debug symbols package: {debug_package_path}")
    return debug_package_path


def remove_debug_symbols_from_wheel(wheel_path: str, debug_symbols: List[Tuple[str, bytes]]):
    """Remove debug symbols from the original wheel and update RECORD file."""
    if not debug_symbols:
        return

    temp_wheel_path = f"{wheel_path}.tmp"
    debug_filenames = [filename for filename, _ in debug_symbols]

    # Read existing RECORD content
    record_content = None
    with zipfile.ZipFile(wheel_path, "r") as wheel:
        for file_info in wheel.infolist():
            if file_info.filename.endswith(".dist-info/RECORD"):
                record_content = wheel.read(file_info.filename).decode("utf-8")
                break

    # Create new wheel without debug symbols
    with zipfile.ZipFile(wheel_path, "r") as source_wheel, zipfile.ZipFile(
        temp_wheel_path, "w", zipfile.ZIP_DEFLATED
    ) as temp_wheel:
        for file_info in source_wheel.infolist():
            if file_info.filename in debug_filenames:
                continue
            elif file_info.filename.endswith(".dist-info/RECORD") and record_content:
                # Update RECORD file to remove debug symbol entries
                updated_record = update_record_file(record_content, debug_filenames)
                temp_wheel.writestr(file_info, updated_record)
            else:
                temp_wheel.writestr(file_info, source_wheel.read(file_info.filename))

    # Replace original wheel with cleaned version
    os.replace(temp_wheel_path, wheel_path)
    print(f"Removed debug symbols from: {wheel_path}")


def update_record_file(record_content: str, files_to_remove: List[str]) -> str:
    """Update the RECORD file to remove entries for deleted files."""
    records = []
    reader = csv.reader(io.StringIO(record_content))

    for row in reader:
        if not row:
            continue
        file_path = row[0]
        if file_path not in files_to_remove:
            records.append(row)

    # Rebuild the RECORD content
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for record in records:
        writer.writerow(record)

    return output.getvalue()


def process_wheel(wheel_path: str, output_dir: str = None):
    """Process a single wheel file."""
    if output_dir is None:
        output_dir = os.path.dirname(wheel_path)

    os.makedirs(output_dir, exist_ok=True)

    print(f"Processing wheel: {wheel_path}")

    # Find debug symbols in the wheel
    debug_symbols = find_debug_symbols_in_wheel(wheel_path)

    if not debug_symbols:
        print("No debug symbols found in wheel")
        return None

    print(f"Found {len(debug_symbols)} debug symbol files")

    # Create separate debug symbols package
    debug_package_path = create_debug_symbols_package(wheel_path, debug_symbols, output_dir)

    # Remove debug symbols from original wheel
    remove_debug_symbols_from_wheel(wheel_path, debug_symbols)

    return debug_package_path


def main():
    parser = argparse.ArgumentParser(description="Extract debug symbols from wheels")
    parser.add_argument("wheel", help="Path to the wheel file")
    parser.add_argument("--output-dir", "-o", help="Output directory for debug symbol packages")

    args = parser.parse_args()

    if not os.path.exists(args.wheel):
        print(f"Error: Wheel file not found: {args.wheel}")
        sys.exit(1)

    try:
        debug_package_path = process_wheel(args.wheel, args.output_dir)
        if debug_package_path:
            print(f"Successfully processed wheel. Debug symbols saved to: {debug_package_path}")
        else:
            print("No debug symbols found in wheel")
    except Exception as e:
        print(f"Error processing wheel: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
