#!/usr/bin/env python3

# CI script for the fuzzing pipeline.
#
# Builds and pushes a Docker image for each Python version matrix entry,
# signs the image, extracts the manifest of fuzz binaries, and starts
# each fuzzer with the fuzzydog CLI.
#
# Expected environment variables (set by .gitlab/fuzz.yml):
#   FUZZ_IMAGE          – registry path, e.g. registry.ddbuild.io/dd-trace-py-fuzz
#   PYTHON_VERSION      – e.g. "3.12"
#   PYTHON_IMAGE_TAG    – e.g. "3.12.0"
#   CI_COMMIT_SHORT_SHA – short SHA from GitLab CI

from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess
import sys
import tempfile


SLACK_CHANNEL = "fuzzing-ops"
TEAM_NAME = "profiling-python"
REPOSITORY_URL = "https://github.com/DataDog/dd-trace-py"
PROJECT_NAME = "dd-trace-py"
FUZZ_TYPE = "libfuzzer"
MANIFEST_PATH = "/tmp/fuzz/build/fuzz_binaries.txt"
MAX_PKG_NAME_LENGTH = 50


@dataclass(frozen=True)
class Config:
    fuzz_image: str
    python_version: str
    python_image_tag: str
    commit_short_sha: str
    git_sha: str

    @classmethod
    def from_env(cls) -> Config:
        git_sha = run_command(["git", "rev-parse", "HEAD"]).stdout.strip()
        return cls(
            fuzz_image=os.environ["FUZZ_IMAGE"],
            python_version=os.environ["PYTHON_VERSION"],
            python_image_tag=os.environ["PYTHON_IMAGE_TAG"],
            commit_short_sha=os.environ["CI_COMMIT_SHORT_SHA"],
            git_sha=git_sha,
        )

    @property
    def fuzz_tag(self) -> str:
        return f"py{self.python_version}-{self.commit_short_sha}"

    @property
    def full_image_ref(self) -> str:
        return f"{self.fuzz_image}:{self.fuzz_tag}"

    @property
    def py_version_compact(self) -> str:
        return self.python_version.replace(".", "")


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


def run_command(cmd, capture_output=True):
    """Run *cmd* and raise on non-zero exit."""
    print(f"+ {' '.join(cmd)}")
    if capture_output:
        return subprocess.run(cmd, check=True, capture_output=True, text=True)
    return subprocess.run(cmd, check=True)


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
    return token


def build_and_push_image(config: Config) -> str:
    """Build the fuzz Docker image, push it, and return the metadata file path."""
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
            f"PYTHON_IMAGE_TAG={config.python_image_tag}",
            "-t",
            config.full_image_ref,
            "--push",
            "--metadata-file",
            metadata_file,
            ".",
        ],
        capture_output=False,
    )
    return metadata_file


def sign_image(config: Config, metadata_file: str) -> None:
    """Sign the pushed image via ddsign."""
    run_command(
        ["ddsign", "sign", config.full_image_ref, "--docker-metadata-file", metadata_file],
    )


def extract_manifest(config: Config) -> list[FuzzBinary]:
    """Pull the manifest from the built image and parse it into FuzzBinary entries."""
    run_command(["docker", "pull", config.full_image_ref])
    result = run_command(
        ["docker", "run", "--rm", config.full_image_ref, "cat", MANIFEST_PATH],
    )
    binaries: list[FuzzBinary] = []
    for line in result.stdout.splitlines():
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
                config.full_image_ref,
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
    print(f"Image: {config.full_image_ref}")

    metadata_file = build_and_push_image(config)
    sign_image(config, metadata_file)
    binaries = extract_manifest(config)
    if not binaries:
        print("No fuzz binaries found in manifest")
        sys.exit(1)
    start_fuzzers(config, binaries)

    print(f"Started {len(binaries)} fuzzer(s)")


if __name__ == "__main__":
    main()
