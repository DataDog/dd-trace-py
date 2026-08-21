#!/usr/bin/env python3
"""Rewrite the version in built wheels.

Usage: patch-wheel-versions.py <src_dir> <dest_dir>

Reads CI_PIPELINE_ID, CI_COMMIT_SHA, PACKAGE_VERSION, and CI_COMMIT_REF_SLUG
from the environment. For each wheel found in <src_dir>, rewrites the version
to major.minor.patch[rc].dev<pipeline_id>+[<local>.]<commit_sha>.

The local segment is:
  - the PEP 440 local segment from PACKAGE_VERSION when one is present, else
  - the CI_COMMIT_REF_SLUG (branch slug), unless the branch is main or a
    release branch of the form X.Y, in which case the local segment is
    omitted and the version becomes major.minor.patch[rc].dev<pipeline_id>+<commit_sha>.

Hyphens and underscores in the local segment are normalized to '.' so the
filename and METADATA match.

The distribution name and import package stay ddtrace.

The following locations inside the wheel are updated:
  - dist-info/ directory prefix in every arcname
  - .data/ directory prefix in every arcname (if present)
  - The "Version:" header in dist-info/METADATA
  - dist-info/RECORD (arcnames and METADATA sha256/size)
  - The output wheel filename
"""

import argparse
import base64
import csv
import hashlib
import io
import os
from pathlib import Path
import re
import sys
import zipfile


# Matches a dist-info directory name: <name>-<version>.dist-info
_DIST_INFO_RE = re.compile(r"^([A-Za-z0-9_.-]+)-([^/]+)\.dist-info/")


def _sha256_b64(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _parse_dist_info_version(names: list[str]) -> tuple[str, str]:
    """Return (pkg_name, version) discovered from the dist-info directory entries."""
    for name in names:
        m = _DIST_INFO_RE.match(name)
        if m:
            return m.group(1), m.group(2)
    raise ValueError("No .dist-info/ directory found in wheel -- cannot determine version")


def _normalize_local(segment: str) -> str:
    """Normalize a raw string to a PEP 440 local-version fragment.

    PEP 440 local-version normal form uses '.'. Wheel filenames cannot contain
    '-' (field delimiter). '_' is allowed as a synonym of '-', but adms compares
    the filename to this dotted form, so use '.' in both filename and METADATA.
    Branch slugs may contain '/'; treat it the same way.
    """
    return segment.replace("-", ".").replace("_", ".").replace("/", ".")


def _is_release_branch(ref: str) -> bool:
    return ref == "main" or bool(re.fullmatch(r"\d+\.\d+", ref))


def _build_patched_version(
    source_version: str,
    pipeline_id: str,
    commit_sha: str,
    branch_ref: str = "",
) -> str:
    """Build major.minor.patch[rc].dev<pipeline_id>+[<local>.]<commit_sha>.

    The local segment is taken from source_version if it carries one, else
    from branch_ref (unless branch_ref is main or a release branch, in which
    case no local segment is emitted).
    """
    public, _, existing_local = source_version.partition("+")
    # Drop an existing .devN so the pipeline id becomes the only dev stamp.
    public = re.sub(r"\.dev\d+$", "", public)
    if existing_local:
        local = _normalize_local(existing_local)
    elif branch_ref and not _is_release_branch(branch_ref):
        local = _normalize_local(branch_ref)
    else:
        local = ""
    commit_sha = _normalize_local(commit_sha)
    if local:
        return f"{public}.dev{pipeline_id}+{local}.{commit_sha}"
    return f"{public}.dev{pipeline_id}+{commit_sha}"


def _rewrite_arcname(arcname: str, old_prefix: str, new_prefix: str) -> str:
    if arcname.startswith(old_prefix):
        return new_prefix + arcname[len(old_prefix) :]
    return arcname


def _patch_wheel(src: Path, dest_dir: Path, new_version: str) -> Path:
    """Patch src wheel version and write the result to dest_dir."""
    with zipfile.ZipFile(src, "r") as zf:
        names = zf.namelist()

        pkg_name, old_version = _parse_dist_info_version(names)
        old_dist_info = f"{pkg_name}-{old_version}.dist-info/"
        new_dist_info = f"{pkg_name}-{new_version}.dist-info/"
        old_data = f"{pkg_name}-{old_version}.data/"
        new_data = f"{pkg_name}-{new_version}.data/"

        record_arc = old_dist_info + "RECORD"
        metadata_arc = old_dist_info + "METADATA"

        if record_arc not in names:
            raise ValueError(f"No RECORD found in {src.name}")
        if metadata_arc not in names:
            raise ValueError(f"No METADATA found in {src.name}")

        # Patch METADATA: replace the "Version:" header value. old_version comes from the
        # dist-info directory name, so a header that spells the same version differently
        # (case, '-'/'_' instead of '.') would not match: fail instead of shipping a wheel
        # whose filename and METADATA disagree.
        raw_metadata = zf.read(metadata_arc)
        new_metadata_bytes, count = re.subn(
            rb"(?m)^(Version:[^\S\n]*)" + re.escape(old_version.encode()) + rb"(?=[^\S\n]*$)",
            rb"\g<1>" + new_version.encode(),
            raw_metadata,
        )
        if count != 1:
            raise ValueError(f"Expected 1 'Version: {old_version}' header in METADATA, found {count}")

        # Build new arcname mapping: old -> new (for dist-info and .data prefixes).
        def _remap(arcname: str) -> str:
            arcname = _rewrite_arcname(arcname, old_dist_info, new_dist_info)
            arcname = _rewrite_arcname(arcname, old_data, new_data)
            return arcname

        # Collect all entries (except RECORD; we regenerate it).
        new_record_arc = new_dist_info + "RECORD"

        record_rows: list[list[str]] = []

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as out:
            for info in zf.infolist():
                new_arc = _remap(info.filename)

                if info.filename == record_arc:
                    # Skip; we write RECORD last.
                    continue

                new_info = zipfile.ZipInfo(new_arc, date_time=info.date_time)
                new_info.compress_type = info.compress_type
                new_info.external_attr = info.external_attr

                if info.filename == metadata_arc:
                    data = new_metadata_bytes
                else:
                    data = zf.read(info.filename)

                out.writestr(new_info, data)
                if not info.is_dir():
                    record_rows.append([new_arc, _sha256_b64(data), str(len(data))])

            # Write RECORD last (its own hash/size row is left empty per PEP 427).
            record_rows.append([new_record_arc, "", ""])
            record_buf = io.StringIO()
            csv.writer(record_buf, lineterminator="\n").writerows(record_rows)
            record_bytes = record_buf.getvalue().encode("utf-8")
            record_info = zipfile.ZipInfo(new_record_arc)
            record_info.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(record_info, record_bytes)

        # Compute the new wheel filename from the old one.
        # Wheel filename format: {name}-{version}-{python}-{abi}-{platform}.whl
        stem = src.stem  # e.g. ddtrace-4.15.0rc1-cp311-cp311-manylinux2014_x86_64
        # Replace only the first occurrence of old_version in the stem (the version segment).
        # The stem starts with "<pkg_name>-<version>-"; replace that prefix.
        old_stem_prefix = f"{pkg_name}-{old_version}-"
        if not stem.startswith(old_stem_prefix):
            raise ValueError(f"Wheel filename {src.name} does not start with expected prefix {old_stem_prefix!r}")
        new_stem = f"{pkg_name}-{new_version}-" + stem[len(old_stem_prefix) :]
        dest = dest_dir / (new_stem + ".whl")

        dest.write_bytes(buf.getvalue())
        return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("src_dir", help="Directory containing the source wheels")
    parser.add_argument("dest_dir", help="Directory to write the patched wheels to")
    args = parser.parse_args()

    pipeline_id = os.environ.get("CI_PIPELINE_ID", "")
    commit_sha = os.environ.get("CI_COMMIT_SHA", "")
    package_version = os.environ.get("PACKAGE_VERSION", "")
    branch_ref = os.environ.get("CI_COMMIT_REF_SLUG", "")
    if not pipeline_id or not commit_sha:
        print(
            "[ERROR] CI_PIPELINE_ID and CI_COMMIT_SHA must be set in the environment",
            file=sys.stderr,
        )
        sys.exit(1)
    if not package_version:
        print("[ERROR] PACKAGE_VERSION must be set in the environment", file=sys.stderr)
        sys.exit(1)
    try:
        new_version = _build_patched_version(package_version, pipeline_id, commit_sha, branch_ref)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Patched version: {new_version}")

    src_dir = Path(args.src_dir)
    dest_dir = Path(args.dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    wheels = sorted(src_dir.glob("*.whl"))
    if not wheels:
        print(f"[WARN] No .whl files found in {src_dir}", file=sys.stderr)
        sys.exit(0)

    errors: list[str] = []
    for wheel in wheels:
        try:
            dest = _patch_wheel(wheel, dest_dir, new_version)
            print(f"  {wheel.name}")
            print(f"  -> {dest.name}")
        except Exception as exc:
            errors.append(f"{wheel.name}: {exc}")
            print(f"[ERROR] {wheel.name}: {exc}", file=sys.stderr)

    if errors:
        print(f"\n[ERROR] {len(errors)} wheel(s) failed to patch", file=sys.stderr)
        sys.exit(1)

    print(f"\nPatched {len(wheels)} wheel(s) -> {dest_dir}")


if __name__ == "__main__":
    main()
