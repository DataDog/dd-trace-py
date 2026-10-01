# Native AppSec WAF integration

All WAF bindings use libddwaf embedded in `ddtrace.internal.native._native`.
There is one Python extension and no runtime backend selector. Native builds
include the WAF on Windows and 64-bit Linux/macOS; 32-bit Linux remains excluded.
Windows support is wired into the consumer but still requires build work and
qualification. The ctypes bindings and standalone DLL downloader are removed.

## Responsibilities

[`src/native/appsec/waf`](../src/native/appsec/waf/README.md) is an AppSec-owned module within
the native crate. It depends on PyO3 0.28.3 and a pinned libddwaf-rust revision, with
libddwaf 2.1.0. The native crate registers it as `_native.ddwaf`.

The native API is `Builder -> Engine -> Context -> Result`:

- Builders accept JSON bytes or Python objects and return acceptance plus
  diagnostics, including rejected and partially accepted configurations.
- Engines are immutable snapshots. Replacing an engine preserves active contexts.
- Contexts own inputs and support ordinary evaluation and RASP subcontexts.
- Results retain native outputs and cache Python materialization. `Stats`
  has seven read-only fields and an explicit `as_dict()` conversion.

`ddtrace.appsec._waf.DDWaf` subclasses the native builder. Python owns
remote configuration bookkeeping, fallback rules, obfuscation, and telemetry.
The processor consumes native `Result`, `RulesetInfo`, and `Stats` directly.
Result scalars are read-only, timings use nanoseconds, and output containers are
materialized once. `prepare()` freezes conversion/evaluation/materialization time.
Rule errors remain structured until trace tagging serializes them. Diagnostic
counts are named `accepted_rules` and `rejected_rules`. `_waf_types.py` contains
only an input annotation; SQL dialects live in `_constants.py`. HTTP and DB
helpers do not import the configuration adapter.

The first remote ASM_DD configuration replaces the default before it is added,
avoiding duplicate rule IDs. When no remote ASM_DD configuration is accepted,
default rules are restored. Accepted configurations are saved as immutable JSON
bytes. After a fork, the processor rebuilds from those bytes on first use rather
than acquiring an inherited native lock. Native PID guards reject inherited
builders, engines, and request contexts, and skip their destruction in the child.

## Conversion semantics

Inputs become owned ddwaf objects directly; no intermediate Rust JSON tree is
created. Existing UTF-8 caches are reused without creating a Python cache.
Uncached Unicode is counted and encoded into the final native string buffer.
Surrogates are ignored, matching `str.encode('utf-8', 'ignore')`. String limits
count bytes and may split a UTF-8 sequence. Bytes preserve arbitrary data and
embedded NULs. Inputs are copied into native ownership before releasing the GIL.

The Python 3.15 limited-ABI workaround cannot access raw Unicode storage. That
build uses Python's UTF-8 encoding with `ignore` followed by a native copy, as
the former ctypes implementation did. Python 3.9–3.14 keep direct conversion.
These checks do not change the tracer's declared Python support range.

Request defaults preserve depth 20, container size 256, and string size 4096.
Explicit conversion limits are bounded at depth 256 and container size 65535.
Compatibility conversion coerces unsupported values and wraps integers modulo 2**64.
Byte-level tests cover storage boundaries, truncation, surrogates, invalid bytes,
cycles, and mutation during conversion. Result caches participate in Python GC.

## Builds and caching

libddwaf-rust generates its FFI with bindgen, so build machines need a shared
libclang in addition to the tracer's existing build tools. `LIBCLANG_PATH` selects
an installation outside the standard LLVM locations. The cached Linux test
image used for qualification needed `libclang-dev`; production CI images need
the same prerequisite verified or provisioned. libclang is a build dependency
and is not included in the tracer wheel.

Normal editable and wheel builds select the native WAF on Windows and 64-bit Unix.
32-bit Linux is outside the supported tracer platform matrix.
`DD_WAF_LINK_MODE` selects `static` (default), `source`, or `system`:

```sh
uv pip install --python /path/to/venv/bin/python -e .
DD_WAF_LINK_MODE=source uv pip install --python /path/to/venv/bin/python -e .
DD_WAF_LINK_MODE=system LIBDDWAF_PREFIX=/path/to/prefix \
  uv pip install --python /path/to/venv/bin/python -e .
```

libddwaf-sys owns target selection, native archive download/extraction, binding
generation, and linking. It stores native inputs in Cargo's build output. There
is no tracer bootstrap, separate native archive cache, or archive checksum pin.
When its build script reruns it may download the release again, even if extracted
files exist. Cargo's offline flag does not restrict the build script's HTTP calls.
Checksum verification, shared archive caching, and offline acquisition are future
upstream improvements.

Extension hashes include the selected features, the native crate, and its
lockfile. An explicit `LIBDDWAF_PREFIX` also contributes its headers and libraries.
Tests, documentation, and native Cargo target directories are excluded from that
native cache key. Source archives include the native module and lockfile. Wheels
omit the former standalone shared libddwaf payload.

`source` compiles libddwaf from the dependency's pinned native source without
downloading a prebuilt archive. A cold Cargo build still requires its sources.
`system` requires `LIBDDWAF_PREFIX` at build time and a compatible shared library
at import time. Distribution builds use this explicit system mode.

`scripts/build-native.py` rebuilds just `_native` in an already installed tracer
environment. Its default features are `waf,stats,ffe`, plus `profiling,crashtracker` on Unix.
It replaces the extension atomically. `--offline` controls Cargo dependency
resolution only. To prevent native archive downloads, supply `LIBDDWAF_PREFIX`
or use source mode with cached Cargo dependencies.

## Windows and other platform gaps

At pinned libddwaf-rust revision `b7569bea223b1eb329944154d95bf804fa9fae67`:

- Windows x64 MSVC can statically link the prebuilt `ddwaf_static.lib`.
- dd-trace-py uses MSVC for Windows builds. Upstream also supports GNU x64,
  which requires `source-static` for static linking; GNU is outside our build matrix.
- Windows ARM64 and x86 are rejected before prefix/source selection. Supporting
  them requires changing that guard, target/archive selection, and ABI/toolchain
  validation. Supplying a prefix alone does not bypass the guard.
- Automatic downloads cover our Linux x86_64/aarch64 GNU and musl and macOS
  x86_64/ARM64 targets. Upstream's extra ARMv7 musl mapping is outside the tracer
  support matrix. Other Linux triplets need source builds or a supplied prefix;
  other operating systems are rejected.
- Bindgen needs shared libclang, including LLVM's `bin` directory on Windows.

Windows x64 uses the same libddwaf-sys acquisition and static-linking path as
other platforms. Windows x86/ARM64 support and wheel qualification remain pending.
System-link Windows builds also need explicit DLL packaging/loading; Cargo's
dynamic-link staging does not install the DLL into a Python package. No Windows
binary was built or tested in this worktree.

## Local qualification

The development worktree is `.worktrees/dd-trace-py-libddwaf` under
datadog-apm-python, on `florentin.labelle/appsec/libddwaf-pyo3-poc`, based on
`521cee0d98f4d5a33a0f394677a53dfe13d5e379`. The macOS environment is CPython
3.14.7. Unchanged C/Cython extensions and distribution entry points were staged
from the baseline checkout with the same ABI; `_native` was rebuilt here with
the usual desktop features. This environment is not a clean full-wheel install.

The current macOS build passed 423 processor, conversion, telemetry, RASP,
API-security, native contract, and packaging checks. Five snapshot cases are
excluded from the combined run; four processor snapshot comparisons also fail
on the unchanged baseline due to service metadata differences and missing
trace-stat snapshot files.

Python formatting and typing, Rust formatting and Clippy, extension hashing,
and source archive checks passed. The archive contains the native module and
lockfile and omits the deleted `_ddwaf` package. Import analysis reports the same
existing cycle and 201 dependency violations, with no new violation edges.
Linux and alternative linking modes need requalification after the module and
result API cleanup.

With a test agent running at localhost:9126:

```sh
DD_TRACE_AGENT_URL=http://127.0.0.1:9126 \
  scripts/run-tests --local-python .venv-poc/bin/python \
  tests/appsec/appsec/test_native_waf.py tests/appsec/appsec/test_ddwaf_fuzz.py \
  tests/appsec/appsec/test_processor.py tests/appsec/appsec/test_telemetry.py \
  tests/appsec/appsec/test_common_modules.py tests/appsec/appsec/test_filesystem.py \
  tests/appsec/appsec/api_security/test_api_security_manager.py \
  tests/appsec/appsec/api_security/test_schema_fuzz.py \
  tests/internal/test_native_waf_packaging.py \
  tests/internal/test_native_waf_platform.py tests/appsec/appsec/test_native_bindings.py \
  -- -o addopts= -q -k 'not snapshot'
scripts/run-tests --local-python .venv-poc/bin/python \
  tests/appsec/appsec/test_native_bindings.py::test_snapshots_subcontexts_threads_and_gc \
  -- -o addopts= -q
```

## HTTP measurements

Compare two installed source checkouts, each with its own native extension:

```sh
scripts/run-benchmarks --waf-http --list
scripts/run-benchmarks --waf-http --local-python .venv-poc/bin/python \
  --baseline /path/to/baseline --candidate . \
  --requests 1000 --repeats 5 --artifacts artifacts/waf-final
```

Each sample starts a fresh Flask server with one Waitress worker, warms 30
requests, then measures 1000 requests over HTTP keepalive from a separate client.
Checkout order alternates across five repetitions. Requests use the real tracer
and default rules. The harness checks AppSec activation, completed WAF traces,
HTTP status, events, and timeouts. Export uses a counting sink; remote config,
IAST, and API-security sampling are disabled. Bodies contain 200 nested rows.

macOS ARM64, Python 3.14.7, normal desktop native features and release profile:

| Workload | Baseline CPU/request | Native CPU/request | CPU reduction |
| --- | ---: | ---: | ---: |
| GET | 499 us | 439 us | 12.1% |
| JSON body | 4070 us | 3466 us | 14.8% |
| Unicode body | 6583 us | 5968 us | 9.3% |
| Blocked GET | 523 us | 465 us | 11.1% |

Values are medians of five samples. Both implementations blocked all 5000
measured attack requests. The native Unicode workload recorded three WAF timeouts
in 5000 requests; the baseline recorded none. All other samples had no timeouts.
Median p50 and p95 latencies improved in every workload. Median RSS decreased
about 3.7–4.2 MiB.
These local measurements show no regression in this matrix; they do not establish
zero regression for every supported platform and workload.

Samples, platform information, and the measured native binary hash are in
[`benchmarks/appsec_waf/results/macos-arm64.json`](../benchmarks/appsec_waf/results/macos-arm64.json).

## Remaining release qualification

Full packaging CI (including libclang availability in build images), Linux HTTP measurements, the supported Python/architecture
matrix, free-threading, complete AppSec/RASP/API-security tests, and worker-fork
tests with all tracer products enabled remain required before release. Direct
CPython Unicode access and the native owned-string writer require a focused
safety review. Static loading remains the default; this change introduces no
lazy loading or arena allocator.
