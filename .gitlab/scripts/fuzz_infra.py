#!/usr/bin/env python3

# CI script for the fuzzing pipeline.
#
# Builds and pushes a single compiled Docker image (all fuzz binaries), then
# creates a thin per-binary image for each target (just overrides FUZZ_TARGET).
# This way the expensive compilation happens once and per-binary images are
# trivial single-layer additions.
#
# Expected environment variables (set by .gitlab/fuzz.yml):
#   FUZZ_IMAGE          – registry path, e.g. registry.ddbuild.io/dd-trace-py-fuzz
#   FUZZ_BASE_IMAGE     – pre-built base image ref, e.g. registry.ddbuild.io/dd-trace-py:vXXX-fuzz_base
#   PYTHON_VERSION      – e.g. "3.12" (must match a pyenv version in fuzz_base)
#   CI_COMMIT_SHORT_SHA – short SHA from GitLab CI

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import subprocess
import sys
import tempfile
import time


SLACK_CHANNEL = "profiling-python-ops"
TEAM_NAME = "profiling-python"
REPOSITORY_URL = "https://github.com/DataDog/dd-trace-py"
PROJECT_NAME = "dd-trace-py"
FUZZ_TYPE = "libfuzzer"
MAX_PKG_NAME_LENGTH = 50


@dataclass(frozen=True)
class Config:
    fuzz_image: str
    fuzz_base_image: str
    python_version: str
    commit_short_sha: str
    git_sha: str

    @classmethod
    def from_env(cls) -> Config:
        git_sha = run_command(["git", "rev-parse", "HEAD"]).stdout.strip()
        git_sha = git_sha.decode("utf-8") if isinstance(git_sha, bytes) else git_sha
        return cls(
            fuzz_image=os.environ["FUZZ_IMAGE"],
            fuzz_base_image=os.environ["FUZZ_BASE_IMAGE"],
            python_version=os.environ["PYTHON_VERSION"],
            commit_short_sha=os.environ["CI_COMMIT_SHORT_SHA"],
            git_sha=git_sha,
        )

    @property
    def py_version_compact(self) -> str:
        return self.python_version.replace(".", "")

    @property
    def compiled_tag(self) -> str:
        """Tag for the compiled base image (contains all binaries, no specific target)."""
        return f"py{self.py_version_compact}-{self.commit_short_sha}-compiled"

    @property
    def compiled_image_ref(self) -> str:
        return f"{self.fuzz_image}:{self.compiled_tag}"

    def binary_tag(self, binary_name: str) -> str:
        """Per-binary image tag, e.g. py310-abc1234-fuzz-echion-strings."""
        safe_name = binary_name.replace("_", "-")
        return f"py{self.py_version_compact}-{self.commit_short_sha}-{safe_name}"

    def binary_image_ref(self, binary_name: str) -> str:
        return f"{self.fuzz_image}:{self.binary_tag(binary_name)}"


@dataclass(frozen=True)
class FuzzBinary:
    pkgname: str
    binary_name: str
    binary_path: str


def get_package_name(binary_name: str, py_version_compact: str) -> str:
    """Build a k8s-label-safe package name: dd-trace-py-py<ver>-<binary>.

    Underscores are replaced with hyphens and the result is truncated to
    MAX_PKG_NAME_LENGTH characters.
    """
    prefix = f"{PROJECT_NAME}-py{py_version_compact}"
    suffix = binary_name[: MAX_PKG_NAME_LENGTH - len(prefix) - 1].replace("_", "-")
    return f"{prefix}-{suffix}"


def run_command(cmd: list[str], capture_output: bool = True, inp: str | None = None):
    """Run *cmd* and raise on non-zero exit."""
    print(f"+ {' '.join(cmd)}")
    if capture_output:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, input=inp)
    return subprocess.run(cmd, check=True, text=True, input=inp)


def get_fuzzydog_token() -> str:
    """Obtain a FUZZYDOG_AUTH_TOKEN from vault.

    The token is fetched via vault's OIDC identity endpoint. It is never
    printed to avoid leaking credentials in CI logs.
    """
    result = run_command(
        ["vault", "read", "-field=token", "identity/oidc/token/security-fuzzing-platform"],
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("vault returned an empty FUZZYDOG_AUTH_TOKEN")

    token = token.decode("utf-8") if isinstance(token, bytes) else token
    return token


def build_and_push_compiled_image(config: Config) -> str:
    """Build the compiled base image (all fuzz binaries) and push it.

    Returns the metadata file path for signing.
    """
    metadata_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json").name
    run_command(
        [
            "docker",
            "buildx",
            "build",
            "--target",
            "build",
            "-f",
            "docker/Dockerfile.fuzz",
            "--build-arg",
            f"FUZZ_BASE_IMAGE={config.fuzz_base_image}",
            "--build-arg",
            f"PYTHON_VERSION={config.python_version}",
            "-t",
            config.compiled_image_ref,
            "--push",
            "--metadata-file",
            metadata_file,
            ".",
        ],
        capture_output=False,
    )
    return metadata_file


def build_and_push_binary_image(config: Config, binary: FuzzBinary) -> str:
    """Build a per-binary image on top of the compiled base.

    Adds a thin layer that sets FUZZ_APP/FUZZ_BUILD_ID and symlinks the binary
    so fuzzydog can find it at /fuzzer/builds/<git_sha>.
    Returns the metadata file path for signing.
    """
    metadata_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json").name
    dockerfile_content = (
        f"FROM {config.compiled_image_ref}\n"
        f"ENV FUZZ_TARGET={binary.binary_name}\n"
        f"ENV FUZZ_APP={binary.pkgname}\n"
        f"ENV FUZZ_BUILD_ID={config.git_sha}\n"
    )
    run_command(
        [
            "docker",
            "buildx",
            "build",
            "-t",
            config.binary_image_ref(binary.binary_name),
            "--push",
            "--metadata-file",
            metadata_file,
            "-",
        ],
        capture_output=False,
        inp=dockerfile_content,
    )
    return metadata_file


def sign_image(image_ref: str, metadata_file: str) -> None:
    """Sign the pushed image via ddsign."""
    run_command(
        ["ddsign", "sign", image_ref, "--docker-metadata-file", metadata_file],
    )


def replicate_image(image_ref: str, metadata_file: str) -> None:
    """Replicate the signed image to us1.ddbuild.io."""
    with open(metadata_file) as f:
        metadata = json.load(f)
    digest = metadata["containerimage.digest"]
    image_with_digest = f"{image_ref}@{digest}"
    run_command(
        ["ddsign", "replicate", "--to", "us1.ddbuild.io", image_with_digest],
    )


def wait_for_replication() -> None:
    """Wait for replication to complete (once, after all images are submitted)."""
    # TODO: remove this fixed sleep once our backend infra has fixed retries on this failure.
    # If it's not enough, the automatic scheduler will pick them up within 24 hours.
    print("Waiting 30s for replication to complete...")
    time.sleep(30)


def extract_manifest(config: Config) -> list[FuzzBinary]:
    """Extract the fuzz-binary manifest via a cached buildx export.

    This also primes the Docker layer cache so the subsequent compiled-image
    push reuses the same layers.
    """
    output_dir = tempfile.mkdtemp()
    run_command(
        [
            "docker",
            "buildx",
            "build",
            "--target",
            "manifest",
            "-f",
            "docker/Dockerfile.fuzz",
            "--build-arg",
            f"FUZZ_BASE_IMAGE={config.fuzz_base_image}",
            "--build-arg",
            f"PYTHON_VERSION={config.python_version}",
            "--output",
            f"type=local,dest={output_dir}",
            ".",
        ],
        capture_output=False,
    )
    manifest_file = os.path.join(output_dir, "fuzz_binaries.txt")
    with open(manifest_file) as f:
        content = f.read()
    binaries: list[FuzzBinary] = []
    for line in content.splitlines():
        binary_path = line.strip()
        if not binary_path:
            continue
        binary_name = os.path.basename(binary_path)
        pkg_name = get_package_name(binary_name, config.py_version_compact)
        binaries.append(FuzzBinary(pkgname=pkg_name, binary_name=binary_name, binary_path=binary_path))
    return binaries


def start_fuzzers(config: Config, binaries: list[FuzzBinary]) -> None:
    """Start every discovered fuzzer with the fuzzydog CLI."""
    for binary in binaries:
        print(f"Starting fuzzer: {binary.pkgname} ({binary.binary_name})")
        run_command(
            [
                "fuzzydog",
                "fuzzer",
                "create",
                binary.pkgname,
                "--image",
                config.binary_image_ref(binary.binary_name),
                "--version",
                config.git_sha,
                "--type",
                FUZZ_TYPE,
                "--team",
                TEAM_NAME,
                "--slack-channel",
                SLACK_CHANNEL,
                "--repository-url",
                REPOSITORY_URL,
            ],
        )


def main() -> None:
    config = Config.from_env()
    os.environ["FUZZYDOG_AUTH_TOKEN"] = get_fuzzydog_token()
    print("FUZZYDOG_AUTH_TOKEN acquired from vault")

    # 1. Extract the manifest (primes Docker build cache).
    binaries = extract_manifest(config)
    if not binaries:
        print("No fuzz binaries found in manifest")
        sys.exit(1)

    # 2. Build and push the compiled base image (once, expensive).
    print(f"\n=== Building compiled base image: {config.compiled_image_ref} ===")
    build_and_push_compiled_image(config)

    # 3. Build, sign, and replicate per-binary images (trivial — one ENV layer each).
    for binary in binaries:
        print(f"\n=== Building per-binary image for {binary.binary_name} ===")
        image_ref = config.binary_image_ref(binary.binary_name)
        metadata_file = build_and_push_binary_image(config, binary)
        sign_image(image_ref, metadata_file)
        replicate_image(image_ref, metadata_file)

    # 4. Single wait for all replications to settle.
    wait_for_replication()

    # 5. Start all fuzzers (each pointing at its per-binary image).
    start_fuzzers(config, binaries)
    print(f"Started {len(binaries)} fuzzer(s)")


if __name__ == "__main__":
    main()
