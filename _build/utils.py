from functools import wraps
import hashlib
import importlib.util
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.error import URLError

from _build.constants import DOWNLOAD_INITIAL_DELAY
from _build.constants import DOWNLOAD_MAX_DELAY
from _build.constants import DOWNLOAD_MAX_RETRIES
from _build.constants import HERE
from _build.constants import RUST_MINIMUM_VERSION
from _build.constants import SCCACHE_COMPILE


def is_64_bit_python():
    return sys.maxsize > (1 << 32)


def retry_download(
    max_attempts=DOWNLOAD_MAX_RETRIES,
    initial_delay=DOWNLOAD_INITIAL_DELAY,
    max_delay=DOWNLOAD_MAX_DELAY,
    backoff_factor=1.618,
):
    """
    Decorator to retry downloads with exponential backoff.
    Handles HTTP 503, 429, network errors from GitHub API, and cargo install failures.
    Retriable errors: HTTP 429 (rate limit), 502, 503, 504, network timeouts, and subprocess errors.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except (HTTPError, URLError, TimeoutError, OSError, subprocess.CalledProcessError) as e:
                    # Check if it's a retriable error
                    is_retriable = False
                    if isinstance(e, HTTPError):
                        # Retry on 429 (rate limit), 502/503/504 (server errors)
                        is_retriable = e.code in (429, 502, 503, 504)
                        error_code = f"HTTP {e.code}"
                    elif isinstance(e, (URLError, TimeoutError)):
                        # Retry on network errors and timeouts
                        is_retriable = True
                        error_code = type(e).__name__
                    elif isinstance(e, OSError):
                        # Retry on connection errors
                        is_retriable = True
                        error_code = type(e).__name__
                    elif isinstance(e, subprocess.CalledProcessError):
                        # Retry on subprocess errors (e.g., cargo install network failures)
                        # These often indicate temporary network issues
                        is_retriable = True
                        error_code = f"subprocess exit code {e.returncode}"
                    else:
                        error_code = type(e).__name__

                    if not is_retriable:
                        print(f"ERROR: Operation failed (non-retriable {error_code}): {e}")
                        raise

                    if attempt == max_attempts - 1:
                        print(f"ERROR: Operation failed after {max_attempts} attempts (last error: {error_code})")
                        raise

                    # Calculate delay with jitter
                    delay = min(initial_delay * (backoff_factor**attempt), max_delay)
                    jitter = random.uniform(0, delay * 0.1)
                    total_delay = delay + jitter

                    print(f"WARNING: Operation failed (attempt {attempt + 1}/{max_attempts}): {error_code} - {e}")
                    print(f"  Retrying in {total_delay:.1f} seconds...")
                    time.sleep(total_delay)

            return func(*args, **kwargs)

        return wrapper

    return decorator


def verify_checksum_from_file(sha256_filename, filename):
    # sha256 File format is ``checksum`` followed by two whitespaces, then ``filename`` then ``\n``
    expected_checksum, expected_filename = list(filter(None, open(sha256_filename, "r").read().strip().split(" ")))
    actual_checksum = hashlib.sha256(open(filename, "rb").read()).hexdigest()
    try:
        assert expected_filename.endswith(Path(filename).name)
        assert expected_checksum == actual_checksum
    except AssertionError:
        print("Checksum verification error: Checksum and/or filename don't match:")
        print("expected checksum: %s" % expected_checksum)
        print("actual checksum: %s" % actual_checksum)
        print("expected filename: %s" % expected_filename)
        print("actual filename: %s" % filename)
        sys.exit(1)


def verify_checksum_from_hash(expected_checksum, filename):
    # sha256 File format is ``checksum`` followed by two whitespaces, then ``filename`` then ``\n``
    actual_checksum = hashlib.sha256(open(filename, "rb").read()).hexdigest()
    try:
        assert expected_checksum == actual_checksum
    except AssertionError:
        print("Checksum verification error: Checksum mismatch:")
        print("expected checksum: %s" % expected_checksum)
        print("actual checksum: %s" % actual_checksum)
        sys.exit(1)


def load_module_from_project_file(mod_name, fname):
    """
    Helper used to load a module from a file in this project

    DEV: Loading this way will by-pass loading all parent modules
         e.g. importing `ddtrace.vendor.psutil.setup` will load `ddtrace/__init__.py`
         which has side effects like loading the tracer
    """
    fpath = HERE / fname

    spec = importlib.util.spec_from_file_location(mod_name, fpath)
    if spec is None:
        raise ImportError(f"Could not find module {mod_name} in {fpath}")
    mod = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(f"Could not load module {mod_name} from {fpath}")
    spec.loader.exec_module(mod)
    return mod


def interpose_sccache():
    """
    Injects sccache into the relevant build commands if it's allowed and we think it'll work
    """
    if not SCCACHE_COMPILE:
        return

    # Check for sccache.  We don't do multi-step failover (e.g., if the path is set, but the binary is invalid)
    # Honor both SCCACHE_PATH and SCCACHE env vars for compatibility with docs
    _sccache_path = os.getenv("SCCACHE_PATH") or os.getenv("SCCACHE") or shutil.which("sccache")
    if _sccache_path is None:
        print("WARNING: sccache not found in SCCACHE_PATH, SCCACHE, or PATH, skipping sccache interposition")
        return
    sccache_path = Path(_sccache_path)
    if sccache_path.is_file() and os.access(sccache_path, os.X_OK):
        # Both the cmake and rust toolchains allow the caller to interpose sccache into the compiler commands, but this
        # misses calls from native extension builds.  So we do the normal Rust thing, but modify CC and CXX to point to
        # a wrapper
        os.environ["DD_SCCACHE_PATH"] = str(sccache_path.resolve())
        os.environ["RUSTC_WRAPPER"] = str(sccache_path.resolve())
        cc_path = next(
            (shutil.which(cmd) for cmd in [os.getenv("CC", ""), "cc", "gcc", "clang"] if shutil.which(cmd)), None
        )
        if cc_path:
            os.environ["DD_CC_OLD"] = cc_path
            os.environ["CC"] = str(sccache_path) + " " + str(cc_path)

        cxx_path = next(
            (shutil.which(cmd) for cmd in [os.getenv("CXX", ""), "c++", "g++", "clang++"] if shutil.which(cmd)), None
        )
        if cxx_path:
            os.environ["DD_CXX_OLD"] = cxx_path
            os.environ["CXX"] = str(sccache_path) + " " + str(cxx_path)


def check_rust_toolchain():
    try:
        rustc_res = subprocess.run(["rustc", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cargo_res = subprocess.run(["cargo", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if rustc_res.returncode != 0:
            raise EnvironmentError("rustc required to build Rust extensions")
        if cargo_res.returncode != 0:
            raise EnvironmentError("cargo required to build Rust extensions")

        # Now check valid minimum versions.  These are hardcoded for now, but should be canonized in some other way
        rustc_ver = rustc_res.stdout.decode().split(" ")[1]
        cargo_ver = cargo_res.stdout.decode().split(" ")[1]

        if rustc_ver < RUST_MINIMUM_VERSION:
            raise EnvironmentError(f"rustc version {RUST_MINIMUM_VERSION} or later required, {rustc_ver} found")
        if cargo_ver < RUST_MINIMUM_VERSION:
            raise EnvironmentError(f"cargo version {RUST_MINIMUM_VERSION} or later required, {cargo_ver} found")
    except FileNotFoundError:
        raise EnvironmentError("Rust toolchain not found. Please install Rust from https://rustup.rs/")
