# Top-level meson.build for dd-trace-py
#
# This replaces the setup.py + setuptools build with meson-python.
# Cython and simple C/C++ extensions are built natively by meson.
# Rust (PyO3) and CMake extensions are orchestrated via custom_target.
#
# Build order:
#   1. Rust extension (_native) → produces headers
#   2. libdd_wrapper (CMake shared lib) → depends on Rust headers
#   3. CMake Python extensions (_memalloc, _ddup, _stack) → link libdd_wrapper
#   4. IAST taint tracking (_native) → standalone CMake with pybind11
#   5. Cython extensions → pure meson
#   6. Simple C/C++ extensions → pure meson
#   7. psutil vendor extension → custom_target via its setup.py

project(
  'ddtrace',
  ['c', 'cpp', 'cython'],
  version: '4.7.0rc1',
  meson_version: '>=1.5.0',
  default_options: [
    'buildtype=release',
    'b_ndebug=if-release',
    'c_std=c11',
    'cpp_std=c++17',
  ],
)

py = import('python').find_installation(pure: false)

host = host_machine.system()
cpu = host_machine.cpu_family()
is_64bit = cpu in ['x86_64', 'aarch64']
is_unix = host in ['linux', 'darwin']

# Python version components used in Cython compile_time_env
# py.language_version() returns "major.minor" only (e.g. "3.14")
py_ver_str = py.language_version()
py_ver_parts = py_ver_str.split('.')
py_major = py_ver_parts[0].to_int()
py_minor = py_ver_parts[1].to_int()

# Get micro version from sysconfig (full version string)
_py_full_ver = run_command(
  py, ['-c', 'import platform; print(platform.python_version())'],
  check: true,
).stdout().strip()
_py_full_parts = _py_full_ver.split('.')
py_micro = _py_full_parts[2].to_int()
py_version_hex = (py_major * 16777216) + (py_minor * 65536) + (py_micro * 256)

# Cython compile-time environment: passed via -E flag
# Format: -E NAME=VALUE,...
_cte = (
  'PY_MAJOR_VERSION=' + py_major.to_string()
  + ',PY_MINOR_VERSION=' + py_minor.to_string()
  + ',PY_MICRO_VERSION=' + py_micro.to_string()
  + ',PY_VERSION_HEX=' + py_version_hex.to_string()
)
cython_args = ['--3str', '-3', '-E', _cte]

# Extension suffix (e.g. ".cpython-314-darwin.so")
ext_suffix = run_command(
  py, ['-c', 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))'],
  check: true,
).stdout().strip()

py_prefix = run_command(
  py, ['-c', 'import sys; print(sys.base_prefix)'],
  check: true,
).stdout().strip()

py_build_key = run_command(
  py,
  [
    '-c',
    'import hashlib, pathlib, sys, sysconfig; '
    + 'digest = hashlib.sha256(); '
    + 'parts = ('
    + 'sys.version, '
    + 'str(pathlib.Path(sys.executable).resolve()), '
    + 'sys.implementation.cache_tag or "", '
    + 'sysconfig.get_config_var("SOABI") or "", '
    + 'sysconfig.get_config_var("EXT_SUFFIX") or ""'
    + '); '
    + '[(digest.update(part.encode("utf-8")), digest.update(b"\\0")) for part in parts]; '
    + 'print(digest.hexdigest()[:12])',
  ],
  check: true,
).stdout().strip()

src_root = meson.project_source_root()
build_root = meson.project_build_root()

# ================================================================
# Pure Python sources
# ================================================================

# Install pure Python package via install_subdir.
# meson-python collects everything installed to the prefix and packages it.
# The exclude lists keep binary sources and large vendored C/C++ out of the wheel.
# Shared libraries are excluded here and installed explicitly from the Meson
# build graph below so clean wheels do not depend on source-tree side effects.
install_subdir(
  'ddtrace',
  install_dir: py.get_install_dir(),
  exclude_directories: [
    # Vendored pybind11 - C++ headers not needed in wheel
    'appsec/_iast/_taint_tracking/_vendor',
    # psutil vendor .c sources handled separately
  ],
  exclude_files: [
    # Cython sources (the built .so files are installed separately)
    '**/*.pyx',
    '**/*.pxd',
    # CMake / C++ sources
    '**/CMakeLists.txt',
    '**/*.cpp',
    '**/*.cc',
    '**/*.c',
    '**/*.h',
    '**/*.hh',
    '**/*.hpp',
    # Shared libraries are installed from Meson targets, not copied from the
    # source tree. This prevents dirty worktrees from leaking stale binaries
    # into clean wheel builds.
    '**/*.so',
    '**/*.dylib',
    '**/*.pyd',
    '**/*.dll',
    # Rust sources
    '**/*.rs',
    '**/*.toml',
    '**/*.lock',
  ],
)

# Note: rules.json and other data files under ddtrace/ are included
# via install_subdir above, so no separate install_data needed.

# ================================================================
# Cython Extensions
# ================================================================

py.extension_module(
  '_tagset',
  sources: ['ddtrace/internal/_tagset.pyx'],
  cython_args: cython_args,
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/internal',
)

py.extension_module(
  '_encoding',
  sources: ['ddtrace/internal/_encoding.pyx'],
  cython_args: cython_args,
  include_directories: include_directories('ddtrace/internal'),
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/internal',
)

py.extension_module(
  'metrics_namespaces',
  sources: ['ddtrace/internal/telemetry/metrics_namespaces.pyx'],
  cython_args: cython_args,
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/internal/telemetry',
)


py.extension_module(
  '_threading',
  sources: ['ddtrace/profiling/_threading.pyx'],
  cython_args: cython_args,
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/profiling',
)

py.extension_module(
  '_task',
  sources: ['ddtrace/profiling/collector/_task.pyx'],
  cython_args: cython_args,
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/profiling/collector',
)

py.extension_module(
  '_exception',
  sources: ['ddtrace/profiling/collector/_exception.pyx'],
  cython_args: cython_args,
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/profiling/collector',
)

py.extension_module(
  '_fast_poisson',
  sources: ['ddtrace/profiling/collector/_fast_poisson.pyx'],
  cython_args: cython_args,
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/profiling/collector',
)

py.extension_module(
  '_sampler',
  sources: ['ddtrace/profiling/collector/_sampler.pyx'],
  cython_args: cython_args,
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/profiling/collector',
)

py.extension_module(
  '_lock',
  sources: ['ddtrace/profiling/collector/_lock.pyx'],
  cython_args: cython_args,
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/profiling/collector',
)

# ================================================================
# Simple C/C++ Extensions
# ================================================================

# _threads: C++20 threading extension
# AIDEV-NOTE: Use compiler ID (not just host OS) to select the right flag style.
# On Windows with MinGW/GCC, MSVC-style flags (/std:c++20, /MT) are treated as
# linker input files and cause "file format not recognised" errors.  The
# override_options below already requests c++20 from meson; the explicit
# cpp_args are only needed for /MT (static CRT on MSVC) and warnings on GCC.
cpp = meson.get_compiler('cpp')
_is_msvc = cpp.get_id() in ['msvc', 'clang-cl']
_threads_cpp_args = _is_msvc ? ['/MT'] : ['-std=c++20', '-Wall', '-Wextra']

py.extension_module(
  '_threads',
  sources: ['ddtrace/internal/_threads.cpp'],
  cpp_args: _threads_cpp_args,
  override_options: ['cpp_std=c++20'],
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/internal',
)

if is_unix
  # IAST stack trace extension — needs Py_BUILD_CORE for CPython internal headers
  py.extension_module(
    '_stacktrace',
    sources: ['ddtrace/appsec/_iast/_stacktrace.c'],
    c_args: ['-DPy_BUILD_CORE'],
    install: true,
    install_dir: py.get_install_dir() / 'ddtrace/appsec/_iast',
  )

  # IAST AST patch extension
  py.extension_module(
    'iastpatch',
    sources: ['ddtrace/appsec/_iast/_ast/iastpatch.c'],
    install: true,
    install_dir: py.get_install_dir() / 'ddtrace/appsec/_iast/_ast',
  )
endif


# ================================================================
# Complex Extensions via custom_target (Rust + CMake)
#
# Design: each custom_target writes its built binary into the Meson build
# directory and installs it explicitly. This keeps wheel/editable builds
# deterministic and avoids depending on pre-existing source-tree artifacts.
# ================================================================

build_helper = files('scripts/meson_build_ext.py')

cargo_target_dir = src_root / 'src' / 'native' / ('target' + py_ver_parts[0] + '.' + py_ver_parts[1] + '-' + py_build_key)

rust_features = 'stats,ffe'
if is_unix and is_64bit
  rust_features = 'stats,ffe,profiling,crashtracker'
endif

native_so = '_native' + ext_suffix
profiling_install_dir = py.get_install_dir() / 'ddtrace/internal/datadog/profiling'

# --- Rust extension: ddtrace.internal.native._native ---
rust_stamp = custom_target(
  '_native_rust',
  output: [native_so],
  command: [
    py, build_helper, 'rust',
    '--src-root', src_root,
    '--cargo-target-dir', cargo_target_dir,
    '--manifest', src_root / 'src/native/Cargo.toml',
    '--features', rust_features,
    '--ext-suffix', ext_suffix,
    '--output', '@OUTPUT0@',
    '--python', py.full_path(),
    '--host', host,
  ],
  build_by_default: true,
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/internal/native',
)

if is_unix and is_64bit
  rust_headers_dir = cargo_target_dir / 'include'

  # --- libdd_wrapper shared library ---
  wrapper_so = 'libdd_wrapper' + ext_suffix

  libdd_wrapper_stamp = custom_target(
    'libdd_wrapper',
    output: [wrapper_so],
    command: [
      py, build_helper, 'cmake',
      '--src-root', src_root,
      '--cmake-src-dir', src_root / 'ddtrace/internal/datadog/profiling/dd_wrapper',
      '--output', '@OUTPUT0@',
      '--install-dir', '@OUTDIR@',
      '--python', py.full_path(),
      '--py-prefix', py_prefix,
      '--ext-suffix', ext_suffix,
      '--build-type', get_option('buildtype') == 'debug' ? 'Debug' : 'RelWithDebInfo',
      '--build-key', py_build_key,
      '--cargo-target-dir', cargo_target_dir,
      '--rust-headers-dir', rust_headers_dir,
      '--component-name', 'dd_wrapper',
      '--native-ext-dir', build_root,
    ],
    depends: [rust_stamp],
    build_by_default: true,
    install: true,
    install_dir: profiling_install_dir,
  )

  # --- _memalloc ---
  memalloc_so = '_memalloc' + ext_suffix

  memalloc_stamp = custom_target(
    '_memalloc',
    output: [memalloc_so],
    command: [
      py, build_helper, 'cmake',
      '--src-root', src_root,
      '--cmake-src-dir', src_root / 'ddtrace/profiling/collector',
      '--output', '@OUTPUT0@',
      '--install-dir', '@OUTDIR@',
      '--python', py.full_path(),
      '--py-prefix', py_prefix,
      '--ext-suffix', ext_suffix,
      '--build-type', get_option('buildtype') == 'debug' ? 'Debug' : 'RelWithDebInfo',
      '--build-key', py_build_key,
      '--cargo-target-dir', cargo_target_dir,
      '--rust-headers-dir', rust_headers_dir,
      '--component-name', 'memalloc',
      '--native-ext-dir', build_root,
      '--dd-wrapper-dir', build_root,
    ],
    depends: [libdd_wrapper_stamp],
    build_by_default: true,
    install: true,
    install_dir: py.get_install_dir() / 'ddtrace/profiling/collector',
  )

  # --- _ddup ---
  ddup_so = '_ddup' + ext_suffix

  ddup_stamp = custom_target(
    '_ddup',
    output: [ddup_so],
    command: [
      py, build_helper, 'cmake',
      '--src-root', src_root,
      '--cmake-src-dir', src_root / 'ddtrace/internal/datadog/profiling/ddup',
      '--output', '@OUTPUT0@',
      '--install-dir', '@OUTDIR@',
      '--python', py.full_path(),
      '--py-prefix', py_prefix,
      '--ext-suffix', ext_suffix,
      '--build-type', get_option('buildtype') == 'debug' ? 'Debug' : 'RelWithDebInfo',
      '--build-key', py_build_key,
      '--cargo-target-dir', cargo_target_dir,
      '--rust-headers-dir', rust_headers_dir,
      '--component-name', 'ddup',
      '--native-ext-dir', build_root,
      '--dd-wrapper-dir', build_root,
    ],
    depends: [libdd_wrapper_stamp],
    build_by_default: true,
    install: true,
    install_dir: py.get_install_dir() / 'ddtrace/internal/datadog/profiling/ddup',
  )

  # --- _stack ---
  stack_so = '_stack' + ext_suffix

  stack_stamp = custom_target(
    '_stack',
    output: [stack_so],
    command: [
      py, build_helper, 'cmake',
      '--src-root', src_root,
      '--cmake-src-dir', src_root / 'ddtrace/internal/datadog/profiling/stack',
      '--output', '@OUTPUT0@',
      '--install-dir', '@OUTDIR@',
      '--python', py.full_path(),
      '--py-prefix', py_prefix,
      '--ext-suffix', ext_suffix,
      '--build-type', get_option('buildtype') == 'debug' ? 'Debug' : 'RelWithDebInfo',
      '--build-key', py_build_key,
      '--cargo-target-dir', cargo_target_dir,
      '--rust-headers-dir', rust_headers_dir,
      '--component-name', 'stack',
      '--native-ext-dir', build_root,
      '--dd-wrapper-dir', build_root,
    ],
    depends: [libdd_wrapper_stamp],
    build_by_default: true,
    install: true,
    install_dir: py.get_install_dir() / 'ddtrace/internal/datadog/profiling/stack',
  )
endif

# --- IAST taint tracking: _native (standalone pybind11+Abseil) ---
# AIDEV-NOTE: _native is not built on Windows, matching setup.py behaviour
# (setup.py guards it with `if platform.system() not in ("Windows", "")`).
# The pybind11 cmake build uses designated initialisers that require C++20 on
# MSVC, and IAST is not supported on Windows anyway.
if host != 'windows'
  iast_native_build_so = 'native_iast_raw' + ext_suffix

  iast_stamp = custom_target(
    '_iast_native',
    output: [iast_native_build_so],
    command: [
      py, build_helper, 'cmake',
      '--src-root', src_root,
      '--cmake-src-dir', src_root / 'ddtrace/appsec/_iast/_taint_tracking',
      '--output', '@OUTPUT0@',
      '--install-dir', '@OUTDIR@',
      '--python', py.full_path(),
      '--py-prefix', py_prefix,
      '--ext-suffix', ext_suffix,
      '--build-type', get_option('buildtype') == 'debug' ? 'Debug' : 'RelWithDebInfo',
      '--build-key', py_build_key,
      '--component-name', 'iast_native',
    ],
    build_by_default: true,
    install: true,
    install_dir: py.get_install_dir() / '.meson-native',
  )

  meson.add_install_script(
    py,
    files('scripts/meson_install_iast_native.py'),
    '--ext-suffix', ext_suffix,
    '--py-install-dir', py.get_install_dir(),
  )
endif

# --- psutil vendor extension ---
if host == 'darwin'
  psutil_so_name = '_psutil_osx' + ext_suffix
elif host == 'linux'
  psutil_so_name = '_psutil_linux' + ext_suffix
elif host == 'windows'
  psutil_so_name = '_psutil_windows' + ext_suffix
else
  psutil_so_name = '_psutil_posix' + ext_suffix
endif

psutil_stamp = custom_target(
  'psutil_vendor',
  output: [psutil_so_name],
  command: [
    py, build_helper, 'psutil',
    '--src-root', src_root,
    '--output', '@OUTPUT0@',
    '--python', py.full_path(),
    '--ext-suffix', ext_suffix,
    '--host', host,
  ],
  build_by_default: true,
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/vendor/psutil',
)

# --- libddwaf download ---
if host == 'darwin'
  libddwaf_ext = '.dylib'
  libddwaf_arch_dir = cpu == 'aarch64' ? 'arm64' : 'x86_64'
elif host == 'linux'
  libddwaf_ext = '.so'
  libddwaf_arch_dir = cpu == 'aarch64' ? 'aarch64' : 'x86_64'
elif host == 'windows'
  libddwaf_ext = '.dll'
  if cpu == 'aarch64'
    libddwaf_arch_dir = 'arm64'
  elif cpu == 'x86'
    libddwaf_arch_dir = 'win32'
  else
    libddwaf_arch_dir = 'x64'
  endif
else
  libddwaf_ext = ''
  libddwaf_arch_dir = ''
endif

libddwaf_stamp = custom_target(
  'libddwaf_download',
  output: ['libddwaf' + libddwaf_ext],
  command: [
    py, build_helper, 'libddwaf',
    '--src-root', src_root,
    '--output', '@OUTPUT0@',
    '--python', py.full_path(),
    '--host', host,
  ],
  build_by_default: true,
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/appsec/_ddwaf/libddwaf' / libddwaf_arch_dir / 'lib',
)
