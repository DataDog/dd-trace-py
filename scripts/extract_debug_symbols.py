#!/usr/bin/env python3
"""
Extract debug symbols from wheels and create separate debug symbol packages.

This script:
1. Processes each .so/.dylib file in the wheel
2. Creates debug symbols (.debug files on Linux, .dSYM bundles on macOS) for each .so/.dylib file
3. Strips debug symbols from the original .so/.dylib files
4. Packages debug symbols into a separate zip file (with proper recursive copying for .dSYM bundles)
5. Updates the wheel with stripped .so/.dylib files
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


def create_dsym_bundle(so_file: str, dsymutil: str) -> Optional[str]:
    """Create a .dSYM bundle for a .so file."""
    dsym_path = Path(so_file).with_suffix(".dSYM")

    print(f"Attempting to create .dSYM bundle for: {so_file}")
    print(f"dsymutil command: {dsymutil} {so_file} -o {dsym_path}")

    try:
        result = subprocess.run([dsymutil, so_file, "-o", str(dsym_path)], capture_output=True, text=True, check=True)

        print(f"dsymutil stdout: {result.stdout}")
        if result.stderr:
            print(f"dsymutil stderr: {result.stderr}")

        # Verify that the .dSYM bundle was created and contains content
        if verify_dsym_bundle(dsym_path):
            return str(dsym_path)
        else:
            print(f"dsymutil succeeded but created empty .dSYM bundle for: {so_file}")
            return None

    except subprocess.CalledProcessError as e:
        print(f"Warning: dsymutil failed with exit code {e.returncode}")
        print(f"dsymutil stdout: {e.stdout}")
        print(f"dsymutil stderr: {e.stderr}")
        return None
    except Exception as e:
        print(f"Warning: Error running dsymutil: {e}")
        return None


def verify_debug_file(debug_path: Path) -> bool:
    """Verify that a Linux .debug file was created successfully and contains content."""
    print(f"Verifying debug file: {debug_path}")

    if not debug_path.exists():
        print(f"  Error: Debug file does not exist: {debug_path}")
        return False

    if not debug_path.is_file():
        print(f"  Error: Debug file is not a regular file: {debug_path}")
        return False

    # Check file size
    file_size = debug_path.stat().st_size
    print(f"  Debug file size: {file_size} bytes")

    if file_size == 0:
        print(f"  Error: Debug file is empty: {debug_path}")
        os.remove(debug_path)
        return False

    # Check if the debug file contains debug sections using objdump
    try:
        result = subprocess.run(["objdump", "-h", str(debug_path)], capture_output=True, text=True, check=True)
        debug_sections = [line for line in result.stdout.split("\n") if ".debug_" in line]
        print(f"  Found {len(debug_sections)} debug sections")

        if debug_sections:
            print("  Debug sections found:")
            for section in debug_sections[:5]:  # Show first 5 sections
                print(f"    {section.strip()}")
            if len(debug_sections) > 5:
                print(f"    ... and {len(debug_sections) - 5} more")
        else:
            # If no debug sections found, check if the file has substantial content
            # Some debug files might contain other types of debug information
            if file_size > 1000:  # More than 1KB
                print(f"  Warning: No debug sections found, but file has substantial content ({file_size} bytes)")
                print("  Accepting debug file as it may contain other debug information")
            else:
                print(f"  Error: Debug file contains no debug sections and is too small: {debug_path}")
                os.remove(debug_path)
                return False

    except (subprocess.CalledProcessError, FileNotFoundError):
        print("  Warning: Could not verify debug sections with objdump")
        # If we can't verify with objdump, just check that the file has content
        if file_size > 0:
            print(f"  Debug file has content ({file_size} bytes), assuming it's valid")
        else:
            print("  Error: Debug file appears to be empty")
            os.remove(debug_path)
            return False

    print(f"Successfully created debug file: {debug_path}")
    return True


def verify_dsym_bundle(dsym_path: Path) -> bool:
    """Verify that a .dSYM bundle was created successfully and contains content."""
    print(f"Verifying .dSYM bundle: {dsym_path}")

    if not dsym_path.exists():
        print(f"  Error: .dSYM bundle does not exist: {dsym_path}")
        return False

    if not dsym_path.is_dir():
        print(f"  Error: .dSYM bundle is not a directory: {dsym_path}")
        return False

    # Check if the .dSYM bundle contains the expected Contents/Resources/DWARF directory
    dwarf_dir = dsym_path / "Contents" / "Resources" / "DWARF"
    print(f"  Checking for DWARF directory: {dwarf_dir}")

    if not dwarf_dir.exists():
        print(f"  Error: DWARF directory does not exist: {dwarf_dir}")
        # List what's actually in the .dSYM bundle
        print("  Contents of .dSYM bundle:")
        for item in dsym_path.rglob("*"):
            print(f"    {item}")
        shutil.rmtree(dsym_path, ignore_errors=True)
        return False

    dwarf_files = list(dwarf_dir.iterdir())
    if not dwarf_files:
        print(f"  Error: DWARF directory is empty: {dwarf_dir}")
        shutil.rmtree(dsym_path, ignore_errors=True)
        return False

    print(f"  Success: Found {len(dwarf_files)} files in DWARF directory")
    for dwarf_file in dwarf_files:
        print(f"    {dwarf_file.name}")

    print(f"Successfully created .dSYM bundle: {dsym_path}")
    return True


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
        try:
            subprocess.run([objcopy, "--only-keep-debug", so_file, debug_out], check=True)

            # Verify that the debug file was created and contains content
            if verify_debug_file(Path(debug_out)):
                # Strip the debug symbols from the .so file
                subprocess.run([strip, "-g", so_file], check=True)

                # Link the debug symbols to the .so file
                subprocess.run([objcopy, "--add-gnu-debuglink", debug_out, so_file], check=True)

                return debug_out
            else:
                print(f"Warning: Failed to create valid debug file for {so_file}")
                return None

        except subprocess.CalledProcessError as e:
            print(f"Warning: objcopy failed to create debug file: {e}")
            return None

    elif current_os == "Darwin":
        dsymutil = shutil.which("dsymutil")
        strip = shutil.which("strip")

        debug_path = None
        if dsymutil:
            # 1) Emit dSYM - let dsymutil handle the detection
            debug_path = create_dsym_bundle(so_file, dsymutil)

        if strip:
            # Strip DWARF + local symbols
            subprocess.run([strip, "-S", "-x", so_file], check=True)
        else:
            print("WARNING: strip not found, skipping symbol stripping", file=sys.stderr)

        return debug_path

    return None


def find_dynamic_libraries_in_wheel(wheel_path: str) -> List[Tuple[str, bytes]]:
    """Find and read .so and .dylib files from a wheel file."""
    dynamic_libs = []

    with zipfile.ZipFile(wheel_path, "r") as wheel:
        for file_info in wheel.infolist():
            if file_info.filename.endswith(".so") or file_info.filename.endswith(".dylib"):
                dynamic_libs.append((file_info.filename, wheel.read(file_info.filename)))

    return dynamic_libs


def process_dynamic_library_from_wheel(lib_filename: str, lib_content: bytes, temp_dir: str) -> Union[str, None]:
    """
    Process a dynamic library (.so or .dylib) from a wheel to create debug symbols.

    Args:
        lib_filename: Original filename in the wheel
        lib_content: Binary content of the dynamic library file
        temp_dir: Temporary directory to work in

    Returns:
        Path to the created debug symbol file, or None if no debug symbols were created
    """
    # Create a temporary file for the dynamic library to process it, preserving directory structure
    lib_path = os.path.join(temp_dir, lib_filename)
    os.makedirs(os.path.dirname(lib_path), exist_ok=True)
    with open(lib_path, "wb") as f:
        f.write(lib_content)

    print(f"Processing dynamic library: {lib_filename}")

    try:
        debug_file = create_and_strip_debug_symbols(lib_path)
        if debug_file:
            print(f"Created debug symbols: {debug_file}")
            return debug_file
        return None
    except Exception as e:
        print(f"Error processing dynamic library {lib_filename}: {e}")
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

                if os.path.isdir(debug_file):
                    # For directories (like .dSYM bundles), recursively add all contents
                    for root, dirs, files in os.walk(debug_file):
                        # Add directories
                        for dir_name in dirs:
                            dir_path = os.path.join(root, dir_name)
                            arc_path = os.path.relpath(dir_path, temp_dir)
                            debug_zip.write(dir_path, arc_path)

                        # Add files
                        for file_name in files:
                            file_path = os.path.join(root, file_name)
                            arc_path = os.path.relpath(file_path, temp_dir)
                            debug_zip.write(file_path, arc_path)
                else:
                    # For regular files, add directly
                    debug_zip.write(debug_file, rel_path)

    print(f"Created debug symbols package: {debug_package_path}")
    return debug_package_path


def update_wheel_with_stripped_dynamic_libraries(wheel_path: str, temp_dir: str):
    """Update the wheel with stripped .so and .dylib files."""
    temp_wheel_path = f"{wheel_path}.tmp"

    # Create new wheel with stripped dynamic library files
    with zipfile.ZipFile(wheel_path, "r") as source_wheel, zipfile.ZipFile(
        temp_wheel_path, "w", zipfile.ZIP_DEFLATED
    ) as temp_wheel:
        for file_info in source_wheel.infolist():
            if file_info.filename.endswith(".so") or file_info.filename.endswith(".dylib"):
                # Replace with stripped version, preserving directory structure
                stripped_lib_path = os.path.join(temp_dir, file_info.filename)
                if os.path.exists(stripped_lib_path):
                    with open(stripped_lib_path, "rb") as f:
                        temp_wheel.writestr(file_info.filename, f.read())
                else:
                    # If stripping failed, keep original
                    temp_wheel.writestr(file_info.filename, source_wheel.read(file_info.filename))
            else:
                temp_wheel.writestr(file_info.filename, source_wheel.read(file_info.filename))

    # Replace original wheel with updated version
    os.replace(temp_wheel_path, wheel_path)
    print(f"Updated wheel with stripped dynamic library files: {wheel_path}")


def process_wheel(wheel_path: str, output_dir: Optional[str] = None) -> Optional[str]:
    """Process a single wheel file."""
    if output_dir is None:
        output_dir = os.path.dirname(wheel_path)

    os.makedirs(output_dir, exist_ok=True)

    print(f"Processing wheel: {wheel_path}")

    # Find and read .so and .dylib files from the wheel
    dynamic_libs = find_dynamic_libraries_in_wheel(wheel_path)

    if not dynamic_libs:
        print("No .so or .dylib files found in wheel")
        return None

    print(f"Found {len(dynamic_libs)} dynamic library files")

    # Create temporary directory for processing
    with tempfile.TemporaryDirectory() as temp_dir:
        debug_files = []

        # Process each dynamic library file from the wheel
        for lib_filename, lib_content in dynamic_libs:
            debug_file = process_dynamic_library_from_wheel(lib_filename, lib_content, temp_dir)
            if debug_file:
                debug_files.append(debug_file)

        if not debug_files:
            print("No debug symbols were created")
            return None

        print(f"Successfully created {len(debug_files)} debug symbol files")

        # Create debug symbols package
        debug_package_path = create_debug_symbols_package(wheel_path, debug_files, output_dir, temp_dir)

        # Update wheel with stripped dynamic library files
        update_wheel_with_stripped_dynamic_libraries(wheel_path, temp_dir)

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
