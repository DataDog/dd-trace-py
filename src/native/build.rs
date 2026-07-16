use build_common::find_rust_lld_dir;
use std::{
    env,
    process::{self, Command},
};

fn system_lld_error(detail: &str) -> ! {
    let target = env::var("TARGET").unwrap_or_else(|_| "Linux musl".to_string());

    eprintln!();
    eprintln!("error: LLVM's LLD linker is required to build ddtrace-native for {target}");
    eprintln!();
    eprintln!("The rustup-provided rust-lld does not support zlib-compressed DWARF on musl.");
    eprintln!("{detail}");
    eprintln!();
    eprintln!("Install LLVM's LLD linker and ensure ld.lld is available in PATH.");
    eprintln!("For Alpine and musllinux images, run: apk add --no-cache lld");
    eprintln!();

    process::exit(1);
}

fn require_system_lld() {
    if !Command::new("ld.lld")
        .arg("--version")
        .output()
        .is_ok_and(|output| output.status.success())
    {
        system_lld_error("No working ld.lld executable was found in PATH.");
    }
}

fn configure_otel_thread_context_export() {
    println!("cargo:rerun-if-changed=tls-dynamic-list.txt");
    println!("cargo:rerun-if-env-changed=PATH");

    let target_env = env::var("CARGO_CFG_TARGET_ENV").unwrap_or_default();
    if target_env == "musl" {
        require_system_lld();
    } else if let Some(rust_lld_dir) = find_rust_lld_dir() {
        println!("cargo:rustc-cdylib-link-arg=-B{}", rust_lld_dir.display());
    }

    println!("cargo:rustc-cdylib-link-arg=-fuse-ld=lld");

    let manifest_dir = env::var("CARGO_MANIFEST_DIR").unwrap();
    println!(
        "cargo:rustc-cdylib-link-arg=-Wl,--version-script={manifest_dir}/tls-dynamic-list.txt"
    );
}

fn main() {
    pyo3_build_config::use_pyo3_cfgs();
    //NOTE(@dmehala): PyO3 doesn't link to `libpython` on MacOS.
    // This set the correct linker arguments for the platform.
    // Source: <https://pyo3.rs/main/building-and-distribution.html#macos>
    if cfg!(target_os = "macos") {
        pyo3_build_config::add_extension_module_link_args();
    }

    if env::var("CARGO_CFG_TARGET_OS").ok().as_deref() == Some("linux") {
        configure_otel_thread_context_export();
    }
}
