fn main() {
    //NOTE(@dmehala): PyO3 doesn't link to `libpython` on MacOS.
    // This set the correct linker arguments for the platform.
    // Source: <https://pyo3.rs/main/building-and-distribution.html#macos>
    if cfg!(target_os = "macos") {
        pyo3_build_config::add_extension_module_link_args();
    }

    // Compile the C wrapper for CPython internal APIs
    // This file defines Py_BUILD_CORE and provides access to internal functions

    // Get Python include directory using the cross-compilation info
    let include_dir = match std::env::var("PYO3_CROSS_INCLUDE_DIR") {
        Ok(dir) => std::path::PathBuf::from(dir),
        Err(_) => {
            // Fallback to using Python's sysconfig
            let output = std::process::Command::new("python")
                .args([
                    "-c",
                    "import sysconfig; print(sysconfig.get_path('include'))",
                ])
                .output()
                .expect("Failed to run python to get include directory");
            std::path::PathBuf::from(String::from_utf8(output.stdout).unwrap().trim())
        }
    };

    // Add internal headers path for CPython internal APIs
    let internal_headers_dir = include_dir.join("internal");

    cc::Build::new()
        .file("cpython_internal.c")
        .include(&include_dir)
        .include(&internal_headers_dir) // Add internal headers directory
        .define("Py_BUILD_CORE", "1")
        .compile("cpython_internal");

    // Tell rustc to link the compiled C library
    println!("cargo:rustc-link-lib=static=cpython_internal");

    // Force linking to libpython to access internal symbols
    // PyO3 normally avoids linking to libpython on Unix, but we need it for internal APIs
    if !cfg!(target_os = "macos") {
        // Get Python version and library info
        let output = std::process::Command::new("python3")
            .args(["-c", "import sysconfig; version = sysconfig.get_config_var('VERSION'); ldlibrary = sysconfig.get_config_var('LDLIBRARY'); libdir = sysconfig.get_config_var('LIBDIR'); print(f'{version}:{ldlibrary}:{libdir}')"])
            .output()
            .expect("Failed to get Python library info");

        let version_info = String::from_utf8(output.stdout).unwrap();
        let parts: Vec<&str> = version_info.trim().split(':').collect();

        if parts.len() == 3 {
            let version = parts[0];
            let ldlibrary = parts[1];
            let libdir = parts[2];

            // Add library directory to search path
            println!("cargo:rustc-link-search=native={}", libdir);

            // Extract library name from LDLIBRARY (e.g., "libpython3.11.so" -> "python3.11")
            if let Some(lib_name) = ldlibrary
                .strip_prefix("lib")
                .and_then(|s| s.strip_suffix(".so"))
            {
                println!("cargo:rustc-link-lib={}", lib_name);
            } else {
                // Fallback to version-based naming
                println!("cargo:rustc-link-lib=python{}", version);
            }
        }
    }
}
