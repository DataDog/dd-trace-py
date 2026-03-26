# ddtrace/internal/datadog/profiling/ddup/meson.build
# _ddup CMake extension.
#
# Defined in a subdir so @OUTDIR@ resolves to
#   build_root/ddtrace/internal/datadog/profiling/ddup/
# making the RPATH $ORIGIN/.. resolve to
#   build_root/ddtrace/internal/datadog/profiling/ (where libdd_wrapper.so lives).

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
    '--build-type', cmake_build_type,
    '--build-key', py_build_key,
    '--cargo-target-dir', cargo_target_dir,
    '--rust-headers-dir', rust_headers_dir,
    '--component-name', 'ddup',
    '--native-ext-dir', build_root / 'ddtrace/internal/native',
  ],
  depends: [libdd_wrapper_stamp],
  build_by_default: true,
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/internal/datadog/profiling/ddup',
)
