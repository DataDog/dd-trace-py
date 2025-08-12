import argparse
import fnmatch
import os
import zipfile
import csv
import io
import hashlib
import base64


def update_record(record_content, removed_files):
    """Update the RECORD file to remove entries for deleted files."""
    # Parse the existing RECORD
    records = []
    reader = csv.reader(io.StringIO(record_content))

    for row in reader:
        if not row:
            continue
        file_path = row[0]
        # Skip files that were removed
        if not any(fnmatch.fnmatch(file_path, pattern) for pattern in removed_files):
            records.append(row)

    # Rebuild the RECORD content
    output = io.StringIO()
    writer = csv.writer(output, lineterminator='\n')
    for record in records:
        writer.writerow(record)

    return output.getvalue()


def remove_from_zip(zip_filename, patterns):
    temp_zip_filename = f"{zip_filename}.tmp"
    removed_files = []
    record_info = None
    record_content = None

    # First pass: identify files to remove and read RECORD
    with zipfile.ZipFile(zip_filename, "r") as source_zip:
        for file in source_zip.infolist():
            if any(fnmatch.fnmatch(file.filename, pattern) for pattern in patterns):
                removed_files.append(file.filename)
            if file.filename.endswith('.dist-info/RECORD'):
                record_info = file
                record_content = source_zip.read(file.filename).decode('utf-8')

    # Second pass: create new zip without removed files and with updated RECORD
    with zipfile.ZipFile(zip_filename, "r") as source_zip, zipfile.ZipFile(
        temp_zip_filename, "w", zipfile.ZIP_DEFLATED
    ) as temp_zip:
        # DEV: Use ZipInfo objects to ensure original file attributes are preserved
        for file in source_zip.infolist():
            if file.filename in removed_files:
                continue
            elif file.filename.endswith('.dist-info/RECORD') and record_content:
                # Update the RECORD file
                updated_record = update_record(record_content, patterns)
                temp_zip.writestr(file, updated_record)
            else:
                temp_zip.writestr(file, source_zip.read(file.filename))

    os.replace(temp_zip_filename, zip_filename)


def parse_args():
    parser = argparse.ArgumentParser(description="Remove specified file types from a ZIP archive.")
    parser.add_argument("zipfile", help="Name of the ZIP file.")
    parser.add_argument("patterns", nargs="+", help="File patterns to remove from the ZIP file.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    remove_from_zip(args.zipfile, args.patterns)
