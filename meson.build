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

src_root = meson.project_source_root()
build_root = meson.project_build_root()

# ================================================================
# Pure Python sources
# ================================================================

# Install pure Python package via install_subdir.
# meson-python collects everything installed to the prefix and packages it.
# The exclude lists keep binary sources and large vendored C/C++ out of the wheel.
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
_threads_cpp_args = host == 'windows' ? ['/std:c++20', '/MT'] : ['-std=c++20', '-Wall', '-Wextra']

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
# Design: each custom_target writes its built .so DIRECTLY into the
# source tree at the canonical location (mirroring how setup.py works
# for editable/in-place builds).  The target produces a stamp file so
# meson can track completion and enforce ordering.
#
# install_subdir('ddtrace', …) installs the full ddtrace tree at install
# time, by which point the build steps have already populated the .so
# files.  No install_data or install_rename gymnastics needed.
# ================================================================

build_helper = files('scripts/meson_build_ext.py')

cargo_target_dir = src_root / 'src' / 'native' / ('target' + py_ver_parts[0] + '.' + py_ver_parts[1])

rust_features = 'stats,ffe'
if is_unix and is_64bit
  rust_features = 'stats,ffe,profiling,crashtracker'
endif

native_so = '_native' + ext_suffix
native_out = src_root / 'ddtrace/internal/native' / native_so

# --- Rust extension: ddtrace.internal.native._native ---
rust_stamp = custom_target(
  '_native_rust',
  output: ['_native_rust.stamp'],
  command: [
    py, build_helper, 'rust',
    '--src-root', src_root,
    '--cargo-target-dir', cargo_target_dir,
    '--manifest', src_root / 'src/native/Cargo.toml',
    '--features', rust_features,
    '--ext-suffix', ext_suffix,
    '--output', native_out,
    '--stamp', '@OUTPUT0@',
    '--python', py.full_path(),
    '--host', host,
  ],
  build_by_default: true,
  install: false,
)

if is_unix and is_64bit
  rust_headers_dir = cargo_target_dir / 'include'
  profiling_dir = src_root / 'ddtrace/internal/datadog/profiling'

  # --- libdd_wrapper shared library ---
  wrapper_out = profiling_dir / ('libdd_wrapper' + ext_suffix)

  libdd_wrapper_stamp = custom_target(
    'libdd_wrapper',
    output: ['libdd_wrapper.stamp'],
    command: [
      py, build_helper, 'cmake',
      '--src-root', src_root,
      '--cmake-src-dir', src_root / 'ddtrace/internal/datadog/profiling/dd_wrapper',
      '--output', wrapper_out,
      '--install-dir', profiling_dir,
      '--stamp', '@OUTPUT0@',
      '--python', py.full_path(),
      '--py-prefix', py_prefix,
      '--ext-suffix', ext_suffix,
      '--build-type', get_option('buildtype') == 'debug' ? 'Debug' : 'RelWithDebInfo',
      '--cargo-target-dir', cargo_target_dir,
      '--rust-headers-dir', rust_headers_dir,
      '--component-name', 'dd_wrapper',
      '--native-ext-dir', src_root / 'ddtrace/internal/native',
    ],
    depends: [rust_stamp],
    build_by_default: true,
    install: false,
  )

  # --- _memalloc ---
  memalloc_out = src_root / 'ddtrace/profiling/collector' / ('_memalloc' + ext_suffix)

  memalloc_stamp = custom_target(
    '_memalloc',
    output: ['_memalloc.stamp'],
    command: [
      py, build_helper, 'cmake',
      '--src-root', src_root,
      '--cmake-src-dir', src_root / 'ddtrace/profiling/collector',
      '--output', memalloc_out,
      '--install-dir', src_root / 'ddtrace/profiling/collector',
      '--stamp', '@OUTPUT0@',
      '--python', py.full_path(),
      '--py-prefix', py_prefix,
      '--ext-suffix', ext_suffix,
      '--build-type', get_option('buildtype') == 'debug' ? 'Debug' : 'RelWithDebInfo',
      '--cargo-target-dir', cargo_target_dir,
      '--rust-headers-dir', rust_headers_dir,
      '--component-name', 'memalloc',
      '--native-ext-dir', src_root / 'ddtrace/internal/native',
      '--dd-wrapper-dir', profiling_dir,
    ],
    depends: [libdd_wrapper_stamp],
    build_by_default: true,
    install: false,
  )

  # --- _ddup ---
  ddup_out = src_root / 'ddtrace/internal/datadog/profiling/ddup' / ('_ddup' + ext_suffix)

  ddup_stamp = custom_target(
    '_ddup',
    output: ['_ddup.stamp'],
    command: [
      py, build_helper, 'cmake',
      '--src-root', src_root,
      '--cmake-src-dir', src_root / 'ddtrace/internal/datadog/profiling/ddup',
      '--output', ddup_out,
      '--install-dir', src_root / 'ddtrace/internal/datadog/profiling/ddup',
      '--stamp', '@OUTPUT0@',
      '--python', py.full_path(),
      '--py-prefix', py_prefix,
      '--ext-suffix', ext_suffix,
      '--build-type', get_option('buildtype') == 'debug' ? 'Debug' : 'RelWithDebInfo',
      '--cargo-target-dir', cargo_target_dir,
      '--rust-headers-dir', rust_headers_dir,
      '--component-name', 'ddup',
      '--native-ext-dir', src_root / 'ddtrace/internal/native',
      '--dd-wrapper-dir', profiling_dir,
    ],
    depends: [libdd_wrapper_stamp],
    build_by_default: true,
    install: false,
  )

  # --- _stack ---
  stack_out = src_root / 'ddtrace/internal/datadog/profiling/stack' / ('_stack' + ext_suffix)

  stack_stamp = custom_target(
    '_stack',
    output: ['_stack.stamp'],
    command: [
      py, build_helper, 'cmake',
      '--src-root', src_root,
      '--cmake-src-dir', src_root / 'ddtrace/internal/datadog/profiling/stack',
      '--output', stack_out,
      '--install-dir', src_root / 'ddtrace/internal/datadog/profiling/stack',
      '--stamp', '@OUTPUT0@',
      '--python', py.full_path(),
      '--py-prefix', py_prefix,
      '--ext-suffix', ext_suffix,
      '--build-type', get_option('buildtype') == 'debug' ? 'Debug' : 'RelWithDebInfo',
      '--cargo-target-dir', cargo_target_dir,
      '--rust-headers-dir', rust_headers_dir,
      '--component-name', 'stack',
      '--native-ext-dir', src_root / 'ddtrace/internal/native',
      '--dd-wrapper-dir', profiling_dir,
    ],
    depends: [libdd_wrapper_stamp],
    build_by_default: true,
    install: false,
  )
endif

# --- IAST taint tracking: _native (standalone pybind11+Abseil) ---
iast_native_out = src_root / 'ddtrace/appsec/_iast/_taint_tracking' / native_so

iast_stamp = custom_target(
  '_iast_native',
  output: ['_iast_native.stamp'],
  command: [
    py, build_helper, 'cmake',
    '--src-root', src_root,
    '--cmake-src-dir', src_root / 'ddtrace/appsec/_iast/_taint_tracking',
    '--output', iast_native_out,
    '--install-dir', src_root / 'ddtrace/appsec/_iast/_taint_tracking',
    '--stamp', '@OUTPUT0@',
    '--python', py.full_path(),
    '--py-prefix', py_prefix,
    '--ext-suffix', ext_suffix,
    '--build-type', get_option('buildtype') == 'debug' ? 'Debug' : 'RelWithDebInfo',
    '--component-name', 'iast_native',
  ],
  build_by_default: true,
  install: false,
)

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

psutil_out = src_root / 'ddtrace/vendor/psutil' / psutil_so_name

psutil_stamp = custom_target(
  'psutil_vendor',
  output: ['psutil_vendor.stamp'],
  command: [
    py, build_helper, 'psutil',
    '--src-root', src_root,
    '--output', psutil_out,
    '--stamp', '@OUTPUT0@',
    '--python', py.full_path(),
    '--ext-suffix', ext_suffix,
    '--host', host,
  ],
  build_by_default: true,
  install: false,
)

# --- libddwaf download ---
libddwaf_stamp = custom_target(
  'libddwaf_download',
  output: ['libddwaf.stamp'],
  command: [
    py, build_helper, 'libddwaf',
    '--src-root', src_root,
    '--output', '@OUTPUT0@',
    '--python', py.full_path(),
    '--host', host,
  ],
  build_by_default: true,
  install: false,
)
