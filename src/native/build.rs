fn main() {
    pyo3_build_config::use_pyo3_cfgs();
    //NOTE(@dmehala): PyO3 doesn't link to `libpython` on MacOS.
    // This set the correct linker arguments for the platform.
    // Source: <https://pyo3.rs/main/building-and-distribution.html#macos>
    if cfg!(target_os = "macos") {
        pyo3_build_config::add_extension_module_link_args();
    }

    // When cross-compiling for Windows ARM64 from x64, cargo's MSVC detection
    // may not add the ARM64 MSVC runtime lib dir to the linker search path,
    // causing LNK1181 for legacy_stdio_definitions.lib and other CRT libs.
    // Emit it explicitly via cargo:rustc-link-search so link.exe gets a
    // /LIBPATH: for the ARM64 CRT directory.
    //
    // VCToolsInstallDir is set by vcvarsall.bat x64_arm64 and points to the
    // MSVC tools root, e.g. "C:\...\VC\Tools\MSVC\14.XX.YYYYY\".
    let target_os = std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    let target_arch = std::env::var("CARGO_CFG_TARGET_ARCH").unwrap_or_default();
    if target_os == "windows" && target_arch == "aarch64" {
        // Tell cargo to re-run this script when these env vars change, so the
        // cached output is invalidated between build environments.
        println!("cargo:rerun-if-env-changed=VCToolsInstallDir");
        println!("cargo:rerun-if-env-changed=VCINSTALLDIR");

        // Strategy 1: Check VCToolsInstallDir (set by vcvarsall, fast path).
        let mut found = false;
        if let Ok(vc_tools_dir) = std::env::var("VCToolsInstallDir") {
            let arm64_lib = std::path::Path::new(vc_tools_dir.trim_end_matches(['\\', '/']))
                .join("lib")
                .join("arm64");
            println!("cargo:warning=ARM64 build: checking MSVC lib dir: {}", arm64_lib.display());
            if arm64_lib.exists() {
                println!("cargo:warning=ARM64 build: adding link-search: {}", arm64_lib.display());
                println!("cargo:rustc-link-search=native={}", arm64_lib.display());
                found = true;
            }
        }

        // Strategy 2: Search all MSVC toolset versions under VCINSTALLDIR.
        // The ARM64 component may install under a different version than VCToolsInstallDir.
        if !found {
            match std::env::var("VCINSTALLDIR") {
                Ok(vc_install_dir) => {
                    let msvc_root =
                        std::path::Path::new(vc_install_dir.trim_end_matches(['\\', '/']))
                            .join("Tools")
                            .join("MSVC");
                    println!(
                        "cargo:warning=ARM64 build: scanning {} for lib\\arm64",
                        msvc_root.display()
                    );
                    if let Ok(entries) = std::fs::read_dir(&msvc_root) {
                        for entry in entries.flatten() {
                            let arm64_lib = entry.path().join("lib").join("arm64");
                            if arm64_lib.exists() {
                                println!(
                                    "cargo:warning=ARM64 build: found lib\\arm64 at {}",
                                    arm64_lib.display()
                                );
                                println!(
                                    "cargo:rustc-link-search=native={}",
                                    arm64_lib.display()
                                );
                                found = true;
                                break;
                            }
                        }
                        if !found {
                            println!("cargo:warning=ARM64 build: scanned all MSVC versions — no lib\\arm64 found (ARM64 CRT not installed)");
                        }
                    } else {
                        println!(
                            "cargo:warning=ARM64 build: MSVC root not readable: {}",
                            msvc_root.display()
                        );
                    }
                }
                Err(_) => {
                    println!("cargo:warning=ARM64 build: VCINSTALLDIR not set — cannot scan MSVC versions (strategy 2 skipped)");
                }
            }
        }

        if !found {
            println!("cargo:warning=ARM64 build: lib\\arm64 not found — creating empty CRT stub libs");
            let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR not set");
            let stub_dir = std::path::Path::new(&out_dir).join("arm64_crt_stubs");
            std::fs::create_dir_all(&stub_dir).ok();

            // A minimal valid COFF archive is just the 8-byte signature.
            // MSVC link.exe opens it, finds no symbols, and continues.
            let empty_archive = b"!<arch>\n";
            for lib_name in &[
                "legacy_stdio_definitions",
                "legacy_stdio_wide_specifiers",
                "msvcrt",
                "vcruntime",
                "ucrt",
                "oldnames",
            ] {
                let lib_path = stub_dir.join(format!("{}.lib", lib_name));
                if std::fs::write(&lib_path, empty_archive).is_ok() {
                    println!("cargo:warning=ARM64 build: created stub {}", lib_path.display());
                }
            }
            println!("cargo:rustc-link-search=native={}", stub_dir.display());
        }
    }
}
