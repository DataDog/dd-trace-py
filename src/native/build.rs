fn main() {
    //NOTE(@dmehala): PyO3 doesn't link to `libpython` on MacOS.
    // This set the correct linker arguments for the platform.
    // Source: <https://pyo3.rs/main/building-and-distribution.html#macos>
    if cfg!(target_os = "macos") {
        pyo3_build_config::add_extension_module_link_args();
    }

    // Compile C sources for crashtracker runtime stack collection
    // This includes the CPython internal API wrappers needed for _Py_DumpTracebackThreads
    let mut build = cc::Build::new();

    // Get Python configuration
    let python_config = pyo3_build_config::get();

    // Find Python include directory using Python sysconfig
    let python_executable = python_config.executable.as_deref().unwrap_or("python");
    let python_include_output = std::process::Command::new(python_executable)
        .args(&["-c", "import sysconfig; print(sysconfig.get_path('include'))"])
        .output()
        .expect("Failed to get Python include directory");

    if python_include_output.status.success() {
        let python_include_dir = String::from_utf8_lossy(&python_include_output.stdout).trim().to_string();
        build.include(&python_include_dir);
    }

    // Set Py_BUILD_CORE to access internal CPython APIs
    build.define("Py_BUILD_CORE", "1");

    // Compile the C source file
    build
        .file("cpython_internal.c")
        .compile("cpython_internal");

    // Tell rustc to link the static library we just built
    println!("cargo:rustc-link-lib=static=cpython_internal");

    // Tell cargo to invalidate the built crate whenever these files change
    println!("cargo:rerun-if-changed=cpython_internal.c");
    println!("cargo:rerun-if-changed=cpython_internal.h");
    println!("cargo:rerun-if-changed=build.rs");
}