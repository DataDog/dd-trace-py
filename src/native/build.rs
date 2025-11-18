fn main() {
    //NOTE(@dmehala): PyO3 doesn't link to `libpython` on MacOS.
    // This set the correct linker arguments for the platform.
    // Source: <https://pyo3.rs/main/building-and-distribution.html#macos>
    if cfg!(target_os = "macos") {
        pyo3_build_config::add_extension_module_link_args();
    }

    // Compile C sources for crashtracker runtime stack collection
    let mut build = cc::Build::new();

    let python_config = pyo3_build_config::get();
    let python_executable = python_config.executable.as_deref().unwrap_or("python");
    let python_include_output = std::process::Command::new(python_executable)
        .args([
            "-c",
            "import sysconfig; print(sysconfig.get_path('include'))",
        ])
        .output()
        .expect("Failed to get Python include directory");

    if python_include_output.status.success() {
        let python_include_dir = String::from_utf8_lossy(&python_include_output.stdout)
            .trim()
            .to_string();
        build.include(&python_include_dir);
    }

    build.define("Py_BUILD_CORE", "1");
    build.file("cpython_internal.c").compile("cpython_internal");

    // Tell rustc to link the static library we just built
    println!("cargo:rustc-link-lib=static=cpython_internal");

    // Link Python static library for _Py_DumpTracebackThreads access
    let python_version_output = std::process::Command::new(python_executable)
        .args([
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ])
        .output()
        .expect("Failed to get Python version");

    if python_version_output.status.success() {
        let python_version = String::from_utf8_lossy(&python_version_output.stdout)
            .trim()
            .to_string();

        // Get the config directory path where static library is located
        let config_dir_output = std::process::Command::new(python_executable)
            .args(["-c", "import sysconfig; print(sysconfig.get_path('stdlib') + '/config-' + sysconfig.get_config_var('LDVERSION') + '-' + sysconfig.get_platform())"])
            .output()
            .expect("Failed to get Python config directory");

        if config_dir_output.status.success() {
            let config_dir = String::from_utf8_lossy(&config_dir_output.stdout)
                .trim()
                .to_string();
            let static_lib_path = format!("{}/libpython{}.a", config_dir, python_version);

            // Check if static library exists and link it
            if std::path::Path::new(&static_lib_path).exists() {
                // Add search path and link to Python static library
                println!("cargo:rustc-link-search=native={}", config_dir);
                println!("cargo:rustc-link-lib=static=python{}", python_version);

                // Link required dependencies for static Python library (Unix only)
                if !cfg!(target_os = "windows") {
                    println!("cargo:rustc-link-lib=dl");
                    println!("cargo:rustc-link-lib=m");
                    println!("cargo:rustc-link-lib=pthread");
                }

                println!(
                    "cargo:warning=Static linking enabled for Python {}: {}",
                    python_version, static_lib_path
                );
            } else {
                // Static library not available - fallback to faulthandler only
                println!(
                    "cargo:warning=Static library not found at {}, using faulthandler fallback only",
                    static_lib_path
                );
            }
        } else {
            // Fallback: faulthandler only
            println!(
                "cargo:warning=Failed to get config directory, using faulthandler fallback only"
            );
        }
    } else {
        // Fallback: faulthandler only
        println!("cargo:warning=Failed to get Python version, using faulthandler fallback only");
    }

    println!("cargo:rerun-if-changed=cpython_internal.c");
    println!("cargo:rerun-if-changed=cpython_internal.h");
    println!("cargo:rerun-if-changed=build.rs");
}
