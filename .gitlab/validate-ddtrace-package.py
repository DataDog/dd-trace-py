#!/usr/bin/env python3
"""
Comprehensive ddtrace package validation script.

Validates that all expected wheels and sdist are present with correct versions.
Uses the packaging library to properly parse and validate filenames.

Expected artifacts:
  - 52 wheels: 6 Python versions × 8 base platforms + 4 versions × win_arm64
  - 1 sdist: source distribution

Usage:
    python3 validate-ddtrace-package.py [wheels_dir]

Environment:
    PACKAGE_VERSION: Version from pyproject.toml (set by "package version" job)
"""

import os
from pathlib import Path
import sys

from packaging.utils import parse_sdist_filename
from packaging.utils import parse_wheel_filename


# Configuration
PYTHON_TAGS = ["cp39", "cp310", "cp311", "cp312", "cp313", "cp314"]
WIN_ARM64_PYTHON_TAGS = ["cp311", "cp312", "cp313", "cp314"]

BASE_PLATFORMS = [
    "macosx_14_0_arm64",
    "macosx_14_0_x86_64",
    "manylinux2014_aarch64.manylinux_2_17_aarch64",
    "manylinux2014_x86_64.manylinux_2_17_x86_64",
    "musllinux_1_2_aarch64",
    "musllinux_1_2_x86_64",
    "win32",
    "win_amd64",
]


def build_expected_set(version: str) -> set[tuple[str, str, str]]:
    """Build set of expected (version, python_tag, platform) tuples."""
    expected: set[tuple[str, str, str]] = set()
    for py_tag in PYTHON_TAGS:
        for platform in BASE_PLATFORMS:
            expected.add((version, py_tag, platform))
        # Add win_arm64 for Python 3.11+
        if py_tag in WIN_ARM64_PYTHON_TAGS:
            expected.add((version, py_tag, "win_arm64"))
    return expected


def reconstruct_wheel_filename(version: str, python_tag: str, platform: str) -> str:
    """Reconstruct wheel filename from components."""
    return f"ddtrace-{version}-{python_tag}-{python_tag}-{platform}.whl"


def validate_sdist(wheels_dir: str, package_version: str) -> tuple[bool, str, str | None]:
    """Validate sdist exists and has correct version.

    Returns:
        tuple: (success: bool, message: str, sdist_name: str or None)
    """
    sdists = list(Path(wheels_dir).glob("*.tar.gz"))

    if len(sdists) == 0:
        return False, "No sdist found", None

    if len(sdists) > 1:
        return False, f"Multiple sdists found: {[s.name for s in sdists]}", None

    sdist_path = sdists[0]

    # Parse sdist filename using packaging library
    try:
        name, version = parse_sdist_filename(sdist_path.name)
        if str(version) != package_version:
            return (
                False,
                f"SDist version {version} != {package_version}",
                sdist_path.name,
            )
        return True, "SDist version matches", sdist_path.name
    except Exception as e:
        return False, f"Failed to parse sdist filename: {e}", sdist_path.name


def parse_actual_wheels(wheels_dir: str) -> tuple[set[tuple[str, str, str]], list[str], int]:
    """Parse actual wheel files.

    Returns:
        tuple: (actual_set: set, errors: list[str], valid_count: int)
    """
    actual: set[tuple[str, str, str]] = set()
    errors: list[str] = []

    for wheel_file in sorted(Path(wheels_dir).glob("*.whl")):
        try:
            name, version, build, tags = parse_wheel_filename(wheel_file.name)
            # Extract python tag - all tags should have the same interpreter
            py_tag = next(iter(tags)).interpreter

            # Extract platform string directly from filename
            # Format: {name}-{version}-{python}-{abi}-{platform}.whl
            # We know: name=ddtrace, abi=python tag (e.g., cp310)
            # So platform is everything after: ddtrace-{version}-{python}-{python}-
            wheel_base = wheel_file.name.replace(".whl", "")
            marker = f"ddtrace-{version}-{py_tag}-{py_tag}-"
            if marker in wheel_base:
                platform = wheel_base.split(marker)[1]
            else:
                raise ValueError(f"Cannot parse platform from {wheel_file.name}")

            actual.add((str(version), py_tag, platform))
        except Exception as e:
            errors.append(f"{wheel_file.name}: {e}")

    return actual, errors, len(actual)


def identify_version_mismatches(
    actual_set: set[tuple[str, str, str]], package_version: str
) -> dict[str, tuple[str, str]]:
    """Identify wheels with wrong versions."""
    mismatches: dict[str, tuple[str, str]] = {}
    for version, py_tag, platform in actual_set:
        if version != package_version:
            key = reconstruct_wheel_filename(version, py_tag, platform)
            mismatches[key] = (package_version, version)
    return mismatches


def main() -> None:
    """Main validation function."""
    # Get arguments
    wheels_dir = sys.argv[1] if len(sys.argv) > 1 else "pywheels"

    # Get version from environment
    package_version: str | None = os.environ.get("PACKAGE_VERSION")
    if not package_version:
        print("[ERROR] PACKAGE_VERSION not set. Ensure 'package version' job ran.")
        sys.exit(1)

    wheels_path = Path(wheels_dir)
    if not wheels_path.exists():
        print(f"[ERROR] Directory not found: {wheels_dir}")
        sys.exit(1)

    print("=" * 70)
    print("DDTrace Package Validation")
    print("=" * 70)
    print(f"Package version (from pyproject.toml): {package_version}")
    print(f"Validating packages in: {wheels_dir}")
    print()

    errors: list[str] = []
    warnings: list[str] = []

    # Phase 1: Environment Check
    print("[Phase 1] Environment Check")
    print(f"✓ PACKAGE_VERSION is set: {package_version}")
    print()

    # Phase 2: SDist Validation
    print("[Phase 2] SDist Validation")
    sdist_ok, sdist_msg, sdist_name = validate_sdist(wheels_dir, package_version)
    if not sdist_ok:
        print(f"✗ {sdist_msg}")
        errors.append(f"SDist validation: {sdist_msg}")
    else:
        print(f"✓ Found sdist: {sdist_name}")
        print(f"✓ {sdist_msg}")
    print()

    # Phase 3: Parse Actual Wheels
    print("[Phase 3] Parsing Actual Wheels")
    actual_set, parse_errors, valid_count = parse_actual_wheels(wheels_dir)

    if parse_errors:
        print(f"✗ Failed to parse {len(parse_errors)} wheel(s):")
        for error in parse_errors:
            print(f"  - {error}")
        errors.append(f"Malformed wheel filenames: {len(parse_errors)}")

    print(f"Found {valid_count} valid wheels")
    print()

    # Phase 4: Build Expected Set
    print("[Phase 4] Building Expected Set")
    expected_set = build_expected_set(package_version)
    print(f"Expected {len(expected_set)} wheels:")
    print(f"  - {len(PYTHON_TAGS)} Python versions (cp39-cp314)")
    print(f"  - {len(BASE_PLATFORMS)} base platforms")
    print(f"  - {len(WIN_ARM64_PYTHON_TAGS)} Python versions with win_arm64")
    print()

    # Phase 5: Set Comparison
    print("[Phase 5] Set Comparison")

    # Missing wheels
    missing_wheels = expected_set - actual_set
    if missing_wheels:
        print(f"✗ Missing {len(missing_wheels)} wheel(s):")
        for version, py_tag, platform in sorted(missing_wheels):
            filename = reconstruct_wheel_filename(version, py_tag, platform)
            print(f"  - {filename}")
        errors.append(f"Missing wheels: {len(missing_wheels)}")

    # Version mismatches (from actual wheels with different version)
    version_mismatches = identify_version_mismatches(actual_set, package_version)
    if version_mismatches:
        print(f"✗ Version mismatch in {len(version_mismatches)} wheel(s):")
        for filename, (expected, actual_ver) in sorted(version_mismatches.items()):
            print(f"  - {filename}")
            print(f"    Expected: {expected}, Actual: {actual_ver}")
        errors.append(f"Version mismatches: {len(version_mismatches)}")

    # Unexpected wheels
    unexpected_wheels = actual_set - expected_set
    # Filter out version mismatches (they're already reported)
    unexpected_non_version = [
        (v, p, pl) for v, p, pl in unexpected_wheels if reconstruct_wheel_filename(v, p, pl) not in version_mismatches
    ]

    if unexpected_non_version:
        print(f"⚠ Unexpected {len(unexpected_non_version)} wheel(s) (warnings only):")
        for version, py_tag, platform in sorted(unexpected_non_version):
            filename = reconstruct_wheel_filename(version, py_tag, platform)
            print(f"  - {filename}")
        warnings.append(f"Unexpected wheels: {len(unexpected_non_version)}")

    # Only print this if no errors in this phase
    if not missing_wheels and not version_mismatches and not unexpected_non_version:
        print("✓ All wheels match expected set")

    print()

    # Phase 6: Report Results
    print("=" * 70)
    print("Validation Summary")
    print("=" * 70)

    if not errors:
        print("✓ SUCCESS - All validation checks passed!")
        print(f"  Total files: {len(actual_set) + (1 if sdist_ok else 0)}")
        print(f"  Wheels: {valid_count}")
        if sdist_ok:
            print("  SDist: 1")
        if warnings:
            print()
            print("Warnings:")
            for warning in warnings:
                print(f"  - {warning}")
        print()
        print("=" * 70)
        sys.exit(0)
    else:
        print(f"✗ FAILED - Found {len(errors)} error(s):")
        for i, error in enumerate(errors, 1):
            print(f"  {i}. {error}")

        if warnings:
            print()
            print("Warnings:")
            for warning in warnings:
                print(f"  - {warning}")

        print()
        print("=" * 70)
        sys.exit(1)


if __name__ == "__main__":
    main()
