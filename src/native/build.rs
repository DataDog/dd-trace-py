/// Locate the `gcc-ld/` shim directory shipped with the Rust toolchain.
///
/// This directory contains an `ld.lld` wrapper that delegates to `rust-lld`. Passing it via `-B`
/// to the C compiler driver makes it discover rust-lld, which handles TLSDESC relocations
/// properly. Mirrors `libdd-otel-thread-ctx-ffi`'s own `build.rs` in libdatadog.
///
/// gnu-target only: the musl target's bundled `rust-lld` still lacks zlib support so musl builds must use the real system `lld` installed in CI instead (see
/// `.gitlab/scripts/build-wheel-helpers.sh`).
fn find_rust_lld_dir() -> Option<std::path::PathBuf> {
    let rustc = std::env::var("RUSTC").unwrap_or_else(|_| "rustc".into());
    let target = std::env::var("TARGET").ok()?;

    let output = std::process::Command::new(&rustc)
        .arg("--print")
        .arg("sysroot")
        .output()
        .ok()?;

    let sysroot = std::str::from_utf8(&output.stdout).ok()?.trim();
    let dir = std::path::PathBuf::from(sysroot)
        .join("lib/rustlib")
        .join(&target)
        .join("bin/gcc-ld");

    dir.join("ld.lld").exists().then_some(dir)
}

fn main() {
    pyo3_build_config::use_pyo3_cfgs();
    //NOTE(@dmehala): PyO3 doesn't link to `libpython` on MacOS.
    // This set the correct linker arguments for the platform.
    // Source: <https://pyo3.rs/main/building-and-distribution.html#macos>
    if cfg!(target_os = "macos") {
        pyo3_build_config::add_extension_module_link_args();
    }

    // Export the TLSDESC thread-local variable `otel_thread_ctx_v1` (a C symbol pulled in from
    // `libdd-otel-thread-ctx`) to the dynamic symbol table so out-of-process readers can discover
    // it, per the Thread Context OTEP. Rust's cdylib linker applies its own auto-generated
    // version script with `local: *`, which hides all symbols not explicitly allowlisted (this
    // crate's own `#[no_mangle]` items only) and also lets the linker relax the TLSDESC access,
    // eliminating the dynsym entry entirely. `--export-dynamic-symbol`/`--dynamic-list` cannot
    // override that wildcard with GNU ld, and GNU ld refuses to merge a second
    // `--version-script`. LLD *can* merge them, so we pass our own version script with an
    // explicit `global:` entry and force linking with `lld`: the toolchain's bundled `rust-lld`
    // on gnu targets (see `find_rust_lld_dir`), or the system `lld` installed in CI on musl
    // targets, which is found via `PATH` since no `-B` is passed there. Mirrors
    // `libdd-otel-thread-ctx-ffi`'s own `build.rs` in libdatadog, which needs the same export for
    // the same symbol.
    if cfg!(target_os = "linux") {
        let target_env = std::env::var("CARGO_CFG_TARGET_ENV").unwrap_or_default();
        if target_env != "musl" {
            if let Some(gcc_ld_dir) = find_rust_lld_dir() {
                println!("cargo:rustc-cdylib-link-arg=-B{}", gcc_ld_dir.display());
            }
        }
        println!("cargo:rustc-cdylib-link-arg=-fuse-ld=lld");
        println!(
            "cargo:rustc-cdylib-link-arg=-Wl,--version-script={}/otel_thread_ctx.version",
            env!("CARGO_MANIFEST_DIR")
        );
    }
}
