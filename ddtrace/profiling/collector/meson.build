# ddtrace/profiling/collector/meson.build
# _memalloc CMake extension.
#
# Defined in a subdir so @OUTDIR@ resolves to
#   build_root/ddtrace/profiling/collector/
# making _memalloc's RPATH ($ORIGIN/../../internal/datadog/profiling) resolve to
#   build_root/ddtrace/internal/datadog/profiling/ (where libdd_wrapper.so lives).
#
# NATIVE_EXTENSION_LOCATION is set to build_root/ddtrace/internal/native so
# that the original find_library() search path in CMakeLists.txt resolves
# ../datadog/profiling to build_root/ddtrace/internal/datadog/profiling/.

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
    '--build-type', cmake_build_type,
    '--build-key', py_build_key,
    '--cargo-target-dir', cargo_target_dir,
    '--rust-headers-dir', rust_headers_dir,
    '--component-name', 'memalloc',
    '--native-ext-dir', build_root / 'ddtrace/internal/native',
  ],
  depends: [libdd_wrapper_stamp],
  build_by_default: true,
  build_always_stale: true,
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/profiling/collector',
)
