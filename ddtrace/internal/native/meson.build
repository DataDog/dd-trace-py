# ddtrace/internal/native/meson.build
# Rust extension: ddtrace.internal.native._native
#
# Defined in a subdir so @OUTDIR@ resolves to
#   build_root/ddtrace/internal/native/
# making _native.so reachable via libdd_wrapper's RPATH
# (@loader_path/../../../native from ddtrace/internal/datadog/profiling/).

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
