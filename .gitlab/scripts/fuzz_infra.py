#!/usr/bin/env python3

# This script enables "0 click onboarding" for new fuzzer in the dd-trace-py repository.
# This means that any new fuzzer should be automatically detected and run in the internal
# infrastructure with enrichments, reporting, triaging, auto fix etc...
# Reports are submitted via Slack, with the channel defined by SLACK_CHANNEL
#
# Requirements:
#
# This scripts assumes that:
# - Each fuzz target is built in a separate build directory named `fuzz` and having a `build.sh` script that builds
# the target.
# - The build script appends the path to the built binary to a "MANIFEST_FILE", allowing the discovery of each fuzz
# target by the script.

from __future__ import annotations

from dataclasses import dataclass
import glob
import os
import subprocess
import sys

import requests


# TODO: replace me to dd-trace-py ops' slack channel once initial onboarding is done
SLACK_CHANNEL = "fuzzing-ops"
TEAM_NAME = "profiling-python"
REPOSITORY_URL = "https://github.com/DataDog/dd-trace-py"
PROJECT_NAME = "dd-trace-py"
# We currently only support libfuzzer for this repository.
FUZZ_TYPE = "libfuzzer"
API_URL = "https://fuzzing-api.us1.ddbuild.io/api/v1"

# Paths and constants for script execution
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FUZZER_BINARY_BASE_PATH = "/tmp/fuzz/build"
MANIFEST_FILE = os.path.join(FUZZER_BINARY_BASE_PATH, "fuzz_binaries.txt")
MAX_PKG_NAME_LENGTH = 50
VAULT_PATH = "vault"


@dataclass(frozen=True)
class FuzzBinary:
    """Represents a built fuzz binary ready for upload."""

    pkgname: str
    binary_name: str
    binary_path: str


def build_and_upload_fuzz(
    team: str = TEAM_NAME,
    slack_channel: str = SLACK_CHANNEL,
    repository_url: str = REPOSITORY_URL,
) -> None:
    git_sha = os.popen("git rev-parse HEAD").read().strip()

    # Step 1: Discover and run all build scripts
    build_scripts = discover_build_scripts(REPO_ROOT)
    if not build_scripts:
        print(f"❌ No fuzz build scripts found under {REPO_ROOT}")
        return

    # Clear any previous manifest file
    if os.path.exists(MANIFEST_FILE):
        os.remove(MANIFEST_FILE)

    for build_script in build_scripts:
        run_build_script(build_script)

    # Step 2: Read the manifest file to discover built binaries
    binaries = read_manifest(MANIFEST_FILE)
    if not binaries:
        print(f"❌ No fuzz binaries found in manifest {MANIFEST_FILE}")
        return

    # Step 3: Upload and create a fuzzer for each binary
    for binary in binaries:
        upload_binary(binary, git_sha)
        create_fuzzer(binary, git_sha, team, slack_channel, repository_url)

    print("✅ Fuzzing infrastructure setup completed successfully!")


def get_package_name(binary_name: str) -> str:
    """
    Generate a package name for the fuzzing platform from a binary name.
    It's prefixed with the repository name so it's easier to filter.
    The package name is limited by k8s labels format: must be < 63 chars, alphamumeric and hyphen.
    """
    return PROJECT_NAME + "-" + binary_name[:MAX_PKG_NAME_LENGTH].replace("_", "-")


def _is_executable(file_path: str) -> bool:
    return os.path.isfile(file_path) and os.access(file_path, os.X_OK)


def discover_build_scripts(repo_root: str) -> list[str]:
    """
    Discover fuzz build scripts by looking for '**/fuzz/build.sh'

    This allows for "0 click onboarding" for new fuzz harnesses.
    """
    build_scripts: list[str] = []
    for build_script in glob.glob(os.path.join(repo_root, "**/fuzz/build.sh"), recursive=True):
        print(f"Found build script: {build_script}")
        build_scripts.append(build_script)
    return build_scripts


def run_build_script(build_script: str) -> None:
    """Run a fuzz build script."""
    fuzz_dir = os.path.dirname(build_script)
    print(f"Building fuzz directory: {fuzz_dir}")

    if not os.path.isfile(build_script):
        raise FileNotFoundError(build_script)

    try:
        result = subprocess.run(
            [build_script],
            cwd=fuzz_dir,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
    except subprocess.CalledProcessError as e:
        print(f"❌ Build script failed with exit code {e.returncode}")
        print(f"Command: {e.cmd}")
        if e.stdout:
            print(f"stdout:\n{e.stdout}")
        if e.stderr:
            print(f"stderr:\n{e.stderr}")
        raise

    print(f"✅ Built fuzzers from {build_script}")


def read_manifest(manifest_path: str) -> list[FuzzBinary]:
    """
    Read the manifest file created by build scripts to discover built binaries.

    Each build script appends its binary path(s) to this file.
    """
    binaries: list[FuzzBinary] = []

    if not os.path.isfile(manifest_path):
        print(f"⚠️ No manifest file found at {manifest_path}")
        return binaries

    with open(manifest_path) as f:
        for line in f:
            binary_path = line.strip()
            if not binary_path:
                continue
            if not os.path.isfile(binary_path):
                print(f"⚠️ Binary listed in manifest not found: {binary_path}")
                continue
            if not _is_executable(binary_path):
                print(f"⚠️ Binary listed in manifest is not executable: {binary_path}")
                continue

            binary_name = os.path.basename(binary_path)
            print(f"Found fuzz binary: {binary_path}")
            binaries.append(
                FuzzBinary(
                    pkgname=get_package_name(binary_name),
                    binary_name=binary_name,
                    binary_path=binary_path,
                )
            )

    return binaries


def create_fuzzer(binary: FuzzBinary, git_sha: str, team: str, slack_channel: str, repository_url: str) -> bool:
    """Register a fuzzer with the fuzzing platform."""
    print(f"Starting fuzzer for {binary.pkgname} ({binary.binary_name})...")
    run_payload = {
        "app": binary.pkgname,
        "debug": False,
        "version": git_sha,
        "type": FUZZ_TYPE,
        "binary": binary.binary_name,
        "team": team,
        "slack_channel": slack_channel,
        "repository_url": repository_url,
    }
    try:
        response = requests.post(
            f"{API_URL}/apps/{binary.pkgname}/fuzzers", headers=get_headers(), json=run_payload, timeout=30
        )
        response.raise_for_status()
        print(f"✅ Started fuzzer for {binary.pkgname} ({binary.binary_name})")
        print(response.json())
    except Exception as e:
        print(f"❌ Failed to start fuzzer for {binary.pkgname} ({binary.binary_name}): {e}")
        return True

    return False


def upload_binary(binary: FuzzBinary, git_sha: str) -> bool:
    """Upload a fuzz binary to the fuzzing platform."""
    try:
        # Get presigned URL so we can use s3 uploading
        print(f"Getting presigned URL for {binary.pkgname} ({binary.binary_name})...")
        presigned_response = requests.post(
            f"{API_URL}/apps/{binary.pkgname}/builds/{git_sha}/url", headers=get_headers(), timeout=30
        )

        presigned_response.raise_for_status()
        presigned_url = presigned_response.json()["data"]["url"]

        print(f"Uploading {binary.pkgname} ({binary.binary_name}) for {git_sha}...")
        with open(binary.binary_path, "rb") as f:
            upload_response = requests.put(presigned_url, data=f, timeout=300)
            upload_response.raise_for_status()
        print(f"✅ Uploaded {binary.binary_name}")
    except Exception as e:
        print(f"❌ Failed to upload binary for {binary.pkgname} ({binary.binary_name}): {e}")
        return True
    return False


def get_headers():
    auth_header = (
        os.popen(f"{VAULT_PATH} read -field=token identity/oidc/token/security-fuzzing-platform").read().strip()
    )
    return {"Authorization": f"Bearer {auth_header}", "Content-Type": "application/json"}


if __name__ == "__main__":
    print("🚀 Starting fuzzing infrastructure setup...")
    try:
        build_and_upload_fuzz()
        print("✅ Fuzzing infrastructure setup completed successfully!")
    except Exception as e:
        print(f"❌ Failed to set up fuzzing infrastructure: {e}")
        sys.exit(1)
