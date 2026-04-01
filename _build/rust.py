import os
import subprocess

from setuptools import Distribution
from setuptools_rust import Binding
from setuptools_rust import RustExtension
from setuptools_rust import build_rust

import _build.constants as _c
from _build.constants import CARGO_TARGET_DIR
from _build.constants import COMPILE_MODE
from _build.constants import CURRENT_OS
from _build.constants import DD_CARGO_ARGS
from _build.constants import DOWNLOAD_MAX_RETRIES
from _build.constants import NATIVE_CRATE
from _build.constants import SERVERLESS_BUILD
from _build.utils import is_64_bit_python
from _build.utils import retry_download


rust_features = ["stats"]
if CURRENT_OS in ("Linux", "Darwin") and is_64_bit_python():
    rust_features.append("profiling")
    if not SERVERLESS_BUILD:
        rust_features.append("crashtracker")
if not SERVERLESS_BUILD:
    rust_features.append("ffe")


class PatchedDistribution(Distribution):
    def __init__(self, attrs=None):
        super().__init__(attrs)
        # Tell ext_hashes about your manually-built Rust artifact

        rust_env = os.environ.copy()
        rust_env["CARGO_TARGET_DIR"] = str(CARGO_TARGET_DIR)
        self.rust_extensions = [
            RustExtension(
                # The Python import path of your extension:
                "ddtrace.internal.native._native",
                # Path to your Cargo.toml so setuptools-rust can infer names
                path=str(NATIVE_CRATE / "Cargo.toml"),
                py_limited_api="auto",
                binding=Binding.PyO3,
                debug=COMPILE_MODE.lower() == "debug",
                features=rust_features,
                env=rust_env,
                args=DD_CARGO_ARGS,
            )
        ]


class CustomBuildRust(build_rust):
    """Custom build_rust command that handles dedup_headers and header copying."""

    def initialize_options(self):
        super().initialize_options()

    def is_installed(self, bin_file):
        """Check if a binary is installed in PATH."""
        for path in os.environ.get("PATH", "").split(os.pathsep):
            if os.path.isfile(os.path.join(path, bin_file)):
                return True
        return False

    def install_dedup_headers(self):
        """Install dedup_headers if not already installed."""
        if not self.is_installed("dedup_headers"):
            # Create retry-wrapped cargo install function
            @retry_download(max_attempts=DOWNLOAD_MAX_RETRIES, initial_delay=2.0)
            def cargo_install_with_retry():
                """Run cargo install with retry on network failures."""
                subprocess.run(
                    [
                        "cargo",
                        "install",
                        "--git",
                        "https://github.com/DataDog/libdatadog",
                        "--bin",
                        "dedup_headers",
                        "tools",
                    ],
                    check=True,
                )

            cargo_install_with_retry()

    def run(self):
        """Run the build process with additional post-processing."""

        has_profiling_feature = False
        for ext in self.distribution.rust_extensions:
            if ext.features and "profiling" in ext.features:
                has_profiling_feature = True
                break

        if _c.IS_EDITABLE or getattr(self, "inplace", False):
            self.inplace = True

        super().run()

        # Check if profiling is enabled and run dedup_headers
        if has_profiling_feature:
            self.install_dedup_headers()

            # Add cargo binary folder to PATH
            home = os.path.expanduser("~")
            cargo_bin = os.path.join(home, ".cargo", "bin")
            dedup_env = os.environ.copy()
            dedup_env["PATH"] = cargo_bin + os.pathsep + os.environ["PATH"]

            # Run dedup_headers on the generated headers
            include_dir = CARGO_TARGET_DIR / "include" / "datadog"
            if include_dir.exists():
                subprocess.run(
                    ["dedup_headers", "common.h", "profiling.h"],
                    cwd=str(include_dir),
                    check=True,
                    env=dedup_env,
                )
