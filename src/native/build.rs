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
        // Try multiple possible config directory patterns since sysconfig.get_platform()
        // can be inconsistent with actual directory names across platforms
        let config_dir_output = std::process::Command::new(python_executable)
            .args(["-c", r#"
import sysconfig
import os
import glob

stdlib = sysconfig.get_path('stdlib')
ldversion = sysconfig.get_config_var('LDVERSION')

# Try to find the actual config directory by globbing
config_pattern = os.path.join(stdlib, f'config-{ldversion}-*')
config_dirs = glob.glob(config_pattern)

if config_dirs:
    print(config_dirs[0])
else:
    # Fallback
    platform = sysconfig.get_platform()
    fallback_dir = os.path.join(stdlib, f'config-{ldversion}-{platform}')
    if os.path.exists(fallback_dir):
        print(fallback_dir)
    else:
        print('')
"#])
            .output()
            .expect("Failed to get Python config directory");

        if config_dir_output.status.success() {
            let config_dir = String::from_utf8_lossy(&config_dir_output.stdout)
                .trim()
                .to_string();
            let static_lib_path = format!("{}/libpython{}.a", config_dir, python_version);

            // Check if static library exists and link it
            if std::path::Path::new(&static_lib_path).exists() {
                // TEMPORARY: Disable static linking to test if it's causing GIL issues
                println!(
                    "cargo:warning=Static linking DISABLED for debugging: {}",
                    static_lib_path
                );

                // Add search path and link to Python static library
                println!("cargo:rustc-link-search=native={}", config_dir);
                println!("cargo:rustc-link-lib=static=python{}", python_version);

                // Link required dependencies for static Python library (Unix only)
                if !cfg!(target_os = "windows") {
                    println!("cargo:rustc-link-lib=dl");
                    println!("cargo:rustc-link-lib=m");
                    println!("cargo:rustc-link-lib=pthread");
                }
            }
        }
    }


    println!("cargo:rerun-if-changed=cpython_internal.c");
    println!("cargo:rerun-if-changed=cpython_internal.h");
    println!("cargo:rerun-if-changed=build.rs");
}
