// AIDEV-NOTE: This module provides Rust implementations for memory allocation profiling
// using the cxx bridge to expose functions to C++. This is the first step in migrating
// the memory profiler from C++ to Rust.

#[cxx::bridge(namespace = "ddtrace::profiling")]
mod ffi {
    extern "Rust" {
        /// A trivial function to test the cxx bridge integration.
        /// Returns the input value multiplied by 2.
        fn rust_multiply_by_two(value: i32) -> i32;
    }
}

/// A trivial function to test the cxx bridge integration.
/// This will be called from C++ code in _memalloc.cpp.
fn rust_multiply_by_two(value: i32) -> i32 {
    value * 2
}

#[cfg(test)]
mod tests {
    use super::ffi::rust_multiply_by_two;

    #[test]
    fn test_multiply_by_two() {
        assert_eq!(rust_multiply_by_two(5), 10);
        assert_eq!(rust_multiply_by_two(0), 0);
        assert_eq!(rust_multiply_by_two(-3), -6);
    }
}

