use pyo3::{types::PyAnyMethods, Python};

use crate::config_cache::lazy_bool::LazyBool;

mod lazy_bool {
    use std::sync::atomic::{AtomicU32, Ordering};

    const UNINIT: u32 = 2;
    const TRUE: u32 = 1;
    const FALSE: u32 = 0;

    pub struct LazyBool(AtomicU32);

    impl LazyBool {
        pub const fn new() -> LazyBool {
            LazyBool(AtomicU32::new(UNINIT))
        }

        pub fn get_or_init<F: FnOnce() -> bool>(&self, f: F) -> bool {
            let val = self.0.load(Ordering::Relaxed);
            match val {
                TRUE => true,
                FALSE => false,
                UNINIT => {
                    let init_val = if f() { TRUE } else { FALSE };
                    self.0
                        .compare_exchange(val, init_val, Ordering::Release, Ordering::Relaxed)
                        .map_or_else(|current_val| current_val, |_old_val| init_val)
                        != FALSE
                }
                _ => unreachable!(),
            }
        }
    }

    #[cfg(test)]
    #[allow(clippy::bool_assert_comparison)]
    mod tests {
        use crate::config_cache::lazy_bool::LazyBool;

        #[test]
        fn test_lazy_bool_true() {
            let val = LazyBool::new();

            assert_eq!(val.get_or_init(|| true), true);
            assert_eq!(val.get_or_init(|| false), true);
        }

        #[test]
        fn test_lazy_bool_false() {
            let val = LazyBool::new();

            assert_eq!(val.get_or_init(|| false), false);
            assert_eq!(val.get_or_init(|| true), false);
        }
    }
}

pub fn _128_bit_trace_id_enabled_cached<'py>(py: Python<'py>) -> bool {
    static VALUE: LazyBool = LazyBool::new();
    VALUE.get_or_init(|| {
        let read_128_bit_trace_id_enabled_config = || {
            py.import("ddtrace.internal.settings._config")?
                .getattr("config")?
                .getattr("_128_bit_trace_id_enabled")?
                .extract::<'_, bool>()
        };
        read_128_bit_trace_id_enabled_config().unwrap_or(true)
    })
}
