import argparse
import fnmatch
import os
import zipfile


def remove_from_zip(zip_filename, patterns):
    temp_zip_filename = f"{zip_filename}.tmp"
    with zipfile.ZipFile(zip_filename, "r") as source_zip, zipfile.ZipFile(
        temp_zip_filename, "w", zipfile.ZIP_DEFLATED
    ) as temp_zip:
        files_to_keep = (
            file for file in source_zip.namelist() if not any(fnmatch.fnmatch(file, pattern) for pattern in patterns)
        )
        for file in files_to_keep:
            temp_zip.writestr(file, source_zip.read(file))
    os.replace(temp_zip_filename, zip_filename)


def parse_args():
    parser = argparse.ArgumentParser(description="Remove specified file types from a ZIP archive.")
    parser.add_argument("zipfile", help="Name of the ZIP file.")
    parser.add_argument("patterns", nargs="+", help="File patterns to remove from the ZIP file.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    remove_from_zip(args.zipfile, args.patterns)
