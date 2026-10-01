# Native WAF bindings

This AppSec-owned module in the tracer native crate implements Python-to-ddwaf
conversion, builders, immutable engines, request contexts, and results. It is
registered as `ddtrace.internal.native._native.ddwaf`; there is one extension.

PyO3 is pinned by the native lockfile to 0.28.3 and libddwaf-rust to
`b7569bea223b1eb329944154d95bf804fa9fae67` (libddwaf 2.1.0).
The WAF feature targets Windows and 64-bit Linux/macOS. Windows qualification
is pending; the pinned dependency rejects Windows ARM64 and 32-bit x86.

## Build

Build prerequisites include Rust, the tracer's usual C/C++ tools, and a shared
libclang for libddwaf-rust's bindgen step. The Debian test image used here
needed `libclang-dev`; use `LIBCLANG_PATH` if it is outside standard LLVM locations. macOS uses the LLVM library provided by its development tools.

From the repository root:

```sh
uv pip install --python /path/to/venv/bin/python -e .
```

Once other tracer extensions and distribution metadata are installed, rebuild
only the embedding extension with the target interpreter:

```sh
/path/to/venv/bin/python scripts/build-native.py
/path/to/venv/bin/python scripts/build-native.py --offline
/path/to/venv/bin/python scripts/build-native.py --link-mode source
LIBDDWAF_PREFIX=/path/to/prefix /path/to/venv/bin/python scripts/build-native.py --link-mode system
```

Static linking is the default. libddwaf-sys selects, downloads, and extracts the
pinned native release into Cargo's build output, then generates bindings and links
it into `_native`. `LIBDDWAF_PREFIX` can supply an existing installation instead:
`include/ddwaf.h` and `lib/libddwaf.*` on Unix, or `lib/ddwaf_static.lib` for
Windows MSVC static builds. `source` compiles native libddwaf instead of downloading
a release archive. `system` links a shared library that must be available when
`_native` imports. For full builds, select these modes with `DD_WAF_LINK_MODE`.

`--offline` controls Cargo dependency resolution; the libddwaf-sys build script
can still download the native release when it runs. For builds without network
access, use an existing `LIBDDWAF_PREFIX` or source mode with cached Cargo inputs.

## API and ownership

`Builder.add_config()` returns acceptance and diagnostics. `Builder.build()`
returns an immutable `Engine`; existing `Context` objects retain their engine
across configuration replacement. A context owns transferred input trees until
libddwaf frees them. Conversion happens while attached to Python; evaluation
releases the GIL. `Result` owns outputs and caches Python materialization. Scalar properties are
read-only; `error_code` distinguishes failures from matches. `duration_ns` measures
libddwaf evaluation and `total_duration_ns` includes conversion/materialization.
`prepare()` materializes outputs once and freezes that total. `RulesetInfo` exposes
the ruleset version, accepted/rejected rule counts, and structured rule errors.
`Stats` is a frozen, fixed-field snapshot, cached on first access; `as_dict()`
explicitly allocates a dictionary.

Builders, engines, and contexts reject use in a different process. Their native
state is never locked or destroyed after inheritance through fork. The tracer
adapter rebuilds an engine from immutable accepted configuration bytes on first
use in the child; an inherited request context remains invalid.

## Tests

The tests cover tracer configuration policy and the native binary directly:

```sh
scripts/run-tests --local-python /path/to/venv/bin/python \
  tests/appsec/appsec/test_native_waf.py tests/appsec/appsec/test_native_bindings.py \
  -- -o addopts= -q
```

`DD_WAF_TEST_EXTENSION=/path/to/lib_native.so` selects a binary compiled for
another interpreter for the isolated native subprocess checks.

See [the integration notes](../../../../docs/appsec-native-waf.md) for conversion
semantics, cache inputs, HTTP measurements, and remaining qualification.
