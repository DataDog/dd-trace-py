fn main() {
    //NOTE(@dmehala): PyO3 doesn't link to `libpython` on MacOS.
    // This set the correct linker arguments for the platform.
    // Source: <https://pyo3.rs/main/building-and-distribution.html#macos>
    if cfg!(target_os = "macos") {
        pyo3_build_config::add_extension_module_link_args();
    }
}
