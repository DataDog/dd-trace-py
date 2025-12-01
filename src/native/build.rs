fn main() {
    //NOTE(@dmehala): PyO3 doesn't link to `libpython` on MacOS.
    // This set the correct linker arguments for the platform.
    // Source: <https://pyo3.rs/main/building-and-distribution.html#macos>
    if cfg!(target_os = "macos") {
        pyo3_build_config::add_extension_module_link_args();
    }

    // AIDEV-NOTE: Build the cxx bridge for the memalloc module.
    // This generates C++ header files that can be included in _memalloc.cpp.
    // The path is relative to the crate root (src/native/)
    cxx_build::bridge("memalloc.rs")
        .flag_if_supported("-std=c++17")
        .compile("memalloc_cxx_bridge");

    println!("cargo:rerun-if-changed=memalloc.rs");
}
