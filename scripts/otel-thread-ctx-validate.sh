#!/usr/bin/env bash
# Validates the OTel thread-context publisher (src/native/otel_thread_ctx.rs) against
# the ctx-sharing-demo `context-reader` conformance tool.
#
# Linux only. Run it via:
#   ./scripts/ddtest ./scripts/otel-thread-ctx-validate.sh              # build a wheel locally
#   ./scripts/ddtest ./scripts/otel-thread-ctx-validate.sh <wheel-or-url>  # test a specific wheel
#
# The optional argument lets you validate a wheel that CI already built (e.g. from the
# dd-trace-py-builds S3 bucket) instead of building one locally, so the exact same binary that
# fails/passes in CI can be reproduced here.
set -euo pipefail

if [ "$(uname -s)" != "Linux" ]; then
    echo "This script must run on Linux: use ./scripts/ddtest ./scripts/otel-thread-ctx-validate.sh" >&2
    exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="${DD_TEST_CACHE_DIR:-$HOME/.cache}/ctx-sharing-demo"
REPO_URL="https://github.com/scottgerring/ctx-sharing-demo.git"
DEMO_DURATION="${DEMO_DURATION:-30}"
WHEEL_ARG="${1:-}"

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

if [ -n "$WHEEL_ARG" ]; then
    case "$WHEEL_ARG" in
        http://*|https://*)
            echo "== downloading wheel: $WHEEL_ARG =="
            # URLs may percent-encode characters valid in wheel filenames (e.g. "+" as "%2B");
            # decode the basename so the local file has a name pip/uv can parse as a version.
            DECODED_NAME="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.unquote(sys.argv[1]))' "$(basename "$WHEEL_ARG")")"
            WHEEL_FILE="$BUILD_DIR/$DECODED_NAME"
            curl --fail --location --silent --show-error -o "$WHEEL_FILE" "$WHEEL_ARG"
            ;;
        *)
            WHEEL_FILE="$WHEEL_ARG"
            ;;
    esac
    echo "using: $WHEEL_FILE"
else
    # Build a real wheel the same way CI does (see build_wheel() in
    # .gitlab/scripts/build-wheel-helpers.sh) instead of a bespoke `cargo build` + manual .so
    # copy, which can silently diverge from what's actually shipped (e.g. skip the cdylib
    # version-script/lld logic in src/native/build.rs entirely).
    echo "== building ddtrace wheel (uv build --wheel) =="
    (cd "$PROJECT_ROOT" && uv build --wheel --out-dir "$BUILD_DIR")
    WHEEL_FILE="$(ls "$BUILD_DIR"/*.whl | head -n 1)"
    echo "built: $WHEEL_FILE"
fi

echo "== installing wheel into a fresh venv =="
VENV_DIR="$BUILD_DIR/venv"
uv venv "$VENV_DIR"
uv pip install --python "$VENV_DIR/bin/python" "$WHEEL_FILE"
PYTHON_BIN="$VENV_DIR/bin/python"

NATIVE_LIB="$("$PYTHON_BIN" -c 'import ddtrace.internal.native._native as m; print(m.__file__)')"
echo "native extension: $NATIVE_LIB"

if [ ! -d "$CACHE_DIR" ]; then
    echo "== cloning ctx-sharing-demo into $CACHE_DIR =="
    git clone --depth 1 "$REPO_URL" "$CACHE_DIR"
fi

READER_DIR="$CACHE_DIR/context-reader"
CHECK_ELF_BIN="$READER_DIR/target/release/check-elf"
VALIDATE_BIN="$READER_DIR/target/release/validate"
TAIL_BIN="$READER_DIR/target/release/tail"

# context-reader's `custom-labels` dependency links against libclang (via bindgen) at build
# time. The container has no libclang-dev/root access, so fetch the prebuilt PyPI wheel once
# into the cache dir (persists across runs) and point bindgen at it with LIBCLANG_PATH.
LIBCLANG_DIR="$HOME/.cache/libclang-pip"
if [ ! -f "$LIBCLANG_DIR/clang/native/libclang.so" ]; then
    echo "== fetching libclang (bindgen dependency for context-reader) =="
    mkdir -p "$LIBCLANG_DIR"
    uv pip install --python "$PYTHON_BIN" --target "$LIBCLANG_DIR" libclang
fi
export LIBCLANG_PATH="$LIBCLANG_DIR/clang/native"
# The pip libclang wheel ships only the shared library, not clang's bundled resource-dir
# headers (stddef.h etc.), so point bindgen at gcc's builtin include dir as a fallback.
GCC_INCLUDE_DIR="$(dirname "$(gcc --print-file-name=stddef.h)")"
export BINDGEN_EXTRA_CLANG_ARGS="-idirafter $GCC_INCLUDE_DIR"

if [ ! -x "$CHECK_ELF_BIN" ] || [ ! -x "$VALIDATE_BIN" ] || [ ! -x "$TAIL_BIN" ]; then
    echo "== building context-reader =="
    (cd "$READER_DIR" && cargo build --release)
fi

echo
echo "== check-elf: static TLS conformance of the native extension =="
"$CHECK_ELF_BIN" "$NATIVE_LIB" --symbol otel_thread_ctx_v1

echo
echo "== starting multithreaded span demo =="
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/otel_thread_ctx_demo.py" --duration "$DEMO_DURATION" &
DEMO_PID=$!

cleanup() {
    kill "$DEMO_PID" 2>/dev/null || true
    wait "$DEMO_PID" 2>/dev/null || true
}
trap cleanup EXIT

sleep 1
echo "demo PID: $DEMO_PID"

echo
echo "== validate: one-shot conformance check against the running demo =="
"$VALIDATE_BIN" "$DEMO_PID" --timeout 15

echo
echo "== tail: sampling thread-context changes for 5s =="
timeout 5 "$TAIL_BIN" "$DEMO_PID" || true

echo
echo "done."
