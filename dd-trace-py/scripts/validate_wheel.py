#!/usr/bin/env python3
"""
Validate that a wheel's contents match its RECORD file.

This script checks:
1. All files in the wheel are listed in RECORD
2. All files in RECORD exist in the wheel
3. File hashes match (for files that have hashes in RECORD)
4. File sizes match
"""

import argparse
import base64
import csv
import hashlib
import io
from pathlib import Path
import sys
import zipfile


def compute_hash(data):
    """Compute the urlsafe base64 encoded SHA256 hash of data."""
    hash_digest = hashlib.sha256(data).digest()
    return base64.urlsafe_b64encode(hash_digest).rstrip(b"=").decode("ascii")


def validate_wheel(wheel_path):
    """Validate that wheel contents match its RECORD file."""
    errors = []

    with zipfile.ZipFile(wheel_path, "r") as wheel:
        # Find the RECORD file
        record_path = None
        for name in wheel.namelist():
            if name.endswith(".dist-info/RECORD"):
                record_path = name
                break

        if not record_path:
            errors.append("No RECORD file found in wheel")
            return errors

        # Parse the RECORD file
        record_content = wheel.read(record_path).decode("utf-8")
        record_entries = {}

        reader = csv.reader(io.StringIO(record_content))
        for row in reader:
            if not row or len(row) < 3:
                continue

            file_path, hash_str, size_str = row[0], row[1], row[2]
            record_entries[file_path] = {"hash": hash_str, "size": int(size_str) if size_str else None}

        # Get all files in the wheel (excluding directories)
        wheel_files = set()
        for name in wheel.namelist():
            # Skip directories (they end with /)
            if not name.endswith("/"):
                wheel_files.add(name)

        record_files = set(record_entries.keys())

        # Check for files in wheel but not in RECORD
        files_not_in_record = wheel_files - record_files
        if files_not_in_record:
            for f in sorted(files_not_in_record):
                errors.append(f"File in wheel but not in RECORD: {f}")

        # Check for files in RECORD but not in wheel
        files_not_in_wheel = record_files - wheel_files
        if files_not_in_wheel:
            for f in sorted(files_not_in_wheel):
                errors.append(f"File in RECORD but not in wheel: {f}")

        # Validate hashes and sizes for files that exist in both
        for file_path in record_files & wheel_files:
            # Skip the RECORD file itself
            if file_path == record_path:
                continue

            record_entry = record_entries[file_path]
            file_data = wheel.read(file_path)

            # Check size
            if record_entry["size"] is not None:
                actual_size = len(file_data)
                if actual_size != record_entry["size"]:
                    errors.append(
                        f"Size mismatch for {file_path}: RECORD says {record_entry['size']}, actual is {actual_size}"
                    )

            # Check hash
            if record_entry["hash"]:
                # Parse the hash format (algorithm=base64hash)
                if "=" in record_entry["hash"]:
                    algo, expected_hash = record_entry["hash"].split("=", 1)
                    if algo == "sha256":
                        actual_hash = compute_hash(file_data)
                        if actual_hash != expected_hash:
                            errors.append(
                                f"Hash mismatch for {file_path}: RECORD says {expected_hash}, actual is {actual_hash}"
                            )
                    else:
                        errors.append(f"Unknown hash algorithm {algo} for {file_path} (expected sha256)")
                else:
                    errors.append(f"Invalid hash format for {file_path}: {record_entry['hash']}")
            # The RECORD file itself should not have a hash
            elif file_path != record_path:
                errors.append(f"No hash recorded for {file_path}")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate wheel RECORD file matches contents")
    parser.add_argument("wheel", help="Path to wheel file to validate")

    args = parser.parse_args()

    wheel_path = Path(args.wheel)
    if not wheel_path.exists():
        print(f"Error: Wheel file not found: {wheel_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Validating {wheel_path.name}...")
    errors = validate_wheel(wheel_path)

    if errors:
        print(f"\n[ERROR] Found {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)

    print("[SUCCESS] Wheel validation passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
