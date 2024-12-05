use crate::taint_range::TaintRangeRefs;
use crate::utils::calculate_hash;
use pyo3::Python;
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

pub struct Initializer {
    tx_map: Mutex<HashMap<u64, TaintRangeRefs>>,
}

impl Initializer {
    pub fn new() -> Self {
        Initializer {
            tx_map: Mutex::new(HashMap::new()),
        }
    }

    /// Access a value in the `tx_map` by key.
    pub fn get_value(&self, py: Python, key: u64) -> Option<TaintRangeRefs> {
        let tx_map = self.tx_map.lock().unwrap();
        tx_map.get(&key).map(|v| {
            v.iter()
                .map(|taint_range_ptr| taint_range_ptr.clone_ref(py))
                .collect()
        })
    }

    /// Resets the `tx_map` by clearing all entries.
    pub fn reset_context(&self) {
        let mut tx_map = self.tx_map.lock().unwrap();
        tx_map.clear();
    }

    /// Store TaintRangeRefs for a given string.
    pub fn store_taint_ranges_for_string(&self, s: &str, value: TaintRangeRefs) {
        let key = calculate_hash(s);
        let mut tx_map = self.tx_map.lock().unwrap();
        tx_map.insert(key, value);
    }

    /// Checks if the tx_map is empty.
    pub fn is_empty(&self) -> bool {
        let tx_map = self.tx_map.lock().unwrap();
        tx_map.is_empty()
    }
}

static INITIALIZER: OnceLock<Initializer> = OnceLock::new();

pub fn initialize() {
    INITIALIZER.get_or_init(|| Initializer::new());
}

pub fn get_initializer() -> &'static Initializer {
    INITIALIZER.get().expect("Initializer not initialized")
}

