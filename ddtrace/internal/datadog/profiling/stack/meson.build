# ddtrace/internal/datadog/profiling/stack/meson.build
# _stack CMake extension.
#
# Defined in a subdir so @OUTDIR@ resolves to
#   build_root/ddtrace/internal/datadog/profiling/stack/
# making the RPATH $ORIGIN/.. resolve to
#   build_root/ddtrace/internal/datadog/profiling/ (where libdd_wrapper.so lives).

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
    '--build-type', cmake_build_type,
    '--build-key', py_build_key,
    '--cargo-target-dir', cargo_target_dir,
    '--rust-headers-dir', rust_headers_dir,
    '--component-name', 'stack',
    '--native-ext-dir', build_root / 'ddtrace/internal/native',
  ],
  depends: [libdd_wrapper_stamp],
  build_by_default: true,
  install: true,
  install_dir: py.get_install_dir() / 'ddtrace/internal/datadog/profiling/stack',
)
