# ddtrace/internal/datadog/profiling/meson.build
# libdd_wrapper shared library (CMake), and subdir calls for _ddup and _stack.
#
# Defined in a subdir so @OUTDIR@ for libdd_wrapper resolves to
#   build_root/ddtrace/internal/datadog/profiling/
# which is exactly where _memalloc's RPATH ($ORIGIN/../../internal/datadog/profiling)
# and _ddup/_stack's RPATH ($ORIGIN/..) will look at runtime.

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
    '--build-type', cmake_build_type,
    '--build-key', py_build_key,
    '--cargo-target-dir', cargo_target_dir,
    '--rust-headers-dir', rust_headers_dir,
    '--component-name', 'dd_wrapper',
    '--native-ext-dir', build_root / 'ddtrace/internal/native',
  ],
  depends: [rust_stamp],
  build_by_default: true,
  install: true,
  install_dir: profiling_install_dir,
)

subdir('ddup')
subdir('stack')
