#!/usr/bin/env python3
"""
Extract debug symbols from wheels and create separate debug symbol packages.

This script:
1. Processes each .so file in the wheel
2. Creates debug symbols (.debug files on Linux, .dSYM bundles on macOS) for each .so file
3. Strips debug symbols from the .so files
4. Packages debug symbols into a separate zip file
5. Updates the wheel with stripped .so files
"""

import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union
import zipfile


def get_debug_symbol_patterns():
    """Get file patterns for debug symbols based on platform."""
    return ["*.debug", "*.dSYM/*"]


def create_and_strip_debug_symbols(so_file: str) -> Union[str, None]:
    """
    Create debug symbols from a shared object and strip them from the original.

    This function replicates the logic from setup.py's try_strip_symbols method.
    Returns the path to the created debug symbol file.
    """
    current_os = platform.system()

    if current_os == "Linux":
        objcopy = shutil.which("objcopy")
        strip = shutil.which("strip")

        if not objcopy:
            print("WARNING: objcopy not found, skipping symbol stripping", file=sys.stderr)
            return None

        if not strip:
            print("WARNING: strip not found, skipping symbol stripping", file=sys.stderr)
            return None

        # Try removing the .llvmbc section from the .so file
        subprocess.run([objcopy, "--remove-section", ".llvmbc", so_file], check=False)

        # Then keep the debug symbols in a separate file
        debug_out = f"{so_file}.debug"
        subprocess.run([objcopy, "--only-keep-debug", so_file, debug_out], check=True)

        # Strip the debug symbols from the .so file
        subprocess.run([strip, "-g", so_file], check=True)

        # Link the debug symbols to the .so file
        subprocess.run([objcopy, "--add-gnu-debuglink", debug_out, so_file], check=True)

        return debug_out

    elif current_os == "Darwin":
        dsymutil = shutil.which("dsymutil")
        strip = shutil.which("strip")

        debug_path = None
        if dsymutil:
            # 1) Emit dSYM
            dsym_path = Path(so_file).with_suffix(".dSYM")
            subprocess.run([dsymutil, so_file, "-o", str(dsym_path)], check=False)
            debug_path = str(dsym_path)

        if strip:
            # Strip DWARF + local symbols
            subprocess.run([strip, "-S", "-x", so_file], check=True)
        else:
            print("WARNING: strip not found, skipping symbol stripping", file=sys.stderr)

        return debug_path

    return None


def find_so_files_in_wheel(wheel_path: str) -> List[Tuple[str, bytes]]:
    """Find and read .so files from a wheel file."""
    so_files = []

    with zipfile.ZipFile(wheel_path, "r") as wheel:
        for file_info in wheel.infolist():
            if file_info.filename.endswith(".so"):
                so_files.append((file_info.filename, wheel.read(file_info.filename)))

    return so_files


def process_so_file_from_wheel(so_filename: str, so_content: bytes, temp_dir: str) -> Union[str, None]:
    """
    Process a .so file from a wheel to create debug symbols.

    Args:
        so_filename: Original filename in the wheel
        so_content: Binary content of the .so file
        temp_dir: Temporary directory to work in

    Returns:
        Path to the created debug symbol file, or None if no debug symbols were created
    """
    # Create a temporary file for the .so to process it, preserving directory structure
    so_path = os.path.join(temp_dir, so_filename)
    os.makedirs(os.path.dirname(so_path), exist_ok=True)
    with open(so_path, "wb") as f:
        f.write(so_content)

    print(f"Processing .so file: {so_filename}")

    try:
        debug_file = create_and_strip_debug_symbols(so_path)
        if debug_file:
            print(f"Created debug symbols: {debug_file}")
            return debug_file
        return None
    except Exception as e:
        print(f"Error processing .so file {so_filename}: {e}")
        return None


def create_debug_symbols_package(wheel_path: str, debug_files: List[str], output_dir: str, temp_dir: str) -> str:
    """Create a separate debug symbols package."""
    wheel_name = Path(wheel_path).stem
    debug_package_name = f"{wheel_name}-debug-symbols.zip"
    debug_package_path = os.path.join(output_dir, debug_package_name)

    with zipfile.ZipFile(debug_package_path, "w", zipfile.ZIP_DEFLATED) as debug_zip:
        for debug_file in debug_files:
            if os.path.exists(debug_file):
                # Add the debug file to the zip, preserving directory structure
                # The debug_file path is relative to temp_dir, so we need to extract the relative path
                rel_path = os.path.relpath(debug_file, temp_dir)
                debug_zip.write(debug_file, rel_path)

    print(f"Created debug symbols package: {debug_package_path}")
    return debug_package_path


def update_wheel_with_stripped_so_files(wheel_path: str, temp_dir: str):
    """Update the wheel with stripped .so files."""
    temp_wheel_path = f"{wheel_path}.tmp"

    # Create new wheel with stripped .so files
    with zipfile.ZipFile(wheel_path, "r") as source_wheel, zipfile.ZipFile(
        temp_wheel_path, "w", zipfile.ZIP_DEFLATED
    ) as temp_wheel:
        for file_info in source_wheel.infolist():
            if file_info.filename.endswith(".so"):
                # Replace with stripped version, preserving directory structure
                stripped_so_path = os.path.join(temp_dir, file_info.filename)
                if os.path.exists(stripped_so_path):
                    with open(stripped_so_path, "rb") as f:
                        temp_wheel.writestr(file_info.filename, f.read())
                else:
                    # If stripping failed, keep original
                    temp_wheel.writestr(file_info.filename, source_wheel.read(file_info.filename))
            else:
                temp_wheel.writestr(file_info.filename, source_wheel.read(file_info.filename))

    # Replace original wheel with updated version
    os.replace(temp_wheel_path, wheel_path)
    print(f"Updated wheel with stripped .so files: {wheel_path}")


def process_wheel(wheel_path: str, output_dir: Optional[str] = None) -> Optional[str]:
    """Process a single wheel file."""
    if output_dir is None:
        output_dir = os.path.dirname(wheel_path)

    os.makedirs(output_dir, exist_ok=True)

    print(f"Processing wheel: {wheel_path}")

    # Find and read .so files from the wheel
    so_files = find_so_files_in_wheel(wheel_path)

    if not so_files:
        print("No .so files found in wheel")
        return None

    print(f"Found {len(so_files)} .so files")

    # Create temporary directory for processing
    with tempfile.TemporaryDirectory() as temp_dir:
        debug_files = []

        # Process each .so file from the wheel
        for so_filename, so_content in so_files:
            debug_file = process_so_file_from_wheel(so_filename, so_content, temp_dir)
            if debug_file:
                debug_files.append(debug_file)

        if not debug_files:
            print("No debug symbols were created")
            return None

        # Create debug symbols package
        debug_package_path = create_debug_symbols_package(wheel_path, debug_files, output_dir, temp_dir)

        # Update wheel with stripped .so files
        update_wheel_with_stripped_so_files(wheel_path, temp_dir)

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
            print("No debug symbols were created")
    except Exception as e:
        print(f"Error processing wheel: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
