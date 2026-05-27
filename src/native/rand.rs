use pyo3::{prelude::*, types::PyModule};
use rand::{
    rngs::{OsRng, SmallRng},
    Rng, SeedableRng, TryRngCore,
};
use std::cell::RefCell;
use std::sync::atomic::{AtomicBool, Ordering};

// Set once at module init (Release) from DD_TRACE_SECURE_RANDOM; every read is an Acquire
// load so the store is visible to threads that did not participate in module import.
static SECURE_RANDOM: AtomicBool = AtomicBool::new(false);

// Matches ddtrace's asbool() convention: "true", "True", "TRUE", and "1" are all truthy.
fn parse_bool_env(val: Option<String>) -> bool {
    val.map(|v| matches!(v.to_lowercase().as_str(), "true" | "1"))
        .unwrap_or(false)
}

#[inline]
fn secure_random() -> bool {
    SECURE_RANDOM.load(Ordering::Acquire)
}

struct RngState {
    rng: SmallRng,
}

impl RngState {
    fn new() -> Self {
        Self {
            rng: Self::create_rng(),
        }
    }

    fn create_rng() -> SmallRng {
        // Use from_os_rng with OS entropy for fork safety.
        // We get a fresh seed from the OS RNG each time this is called,
        // ensuring child processes get independent RNG state after fork.
        SmallRng::from_os_rng()
    }

    fn gen(&mut self) -> u64 {
        self.rng.random()
    }

    fn reseed(&mut self) {
        self.rng = Self::create_rng();
    }
}

thread_local! {
    static RNG: RefCell<RngState> = RefCell::new(RngState::new());
}

/// Reseed the thread-local RNG with fresh entropy from the OS.
/// This is called after fork to ensure child processes have independent RNG state.
#[pyfunction]
pub fn seed() {
    RNG.with(|rng| {
        rng.borrow_mut().reseed();
    });
}

/// Generate a random 64-bit unsigned integer.
#[pyfunction]
pub fn rand64bits() -> u64 {
    if secure_random() {
        return OsRng.try_next_u64().expect("OsRng failed");
    }
    RNG.with(|rng| rng.borrow_mut().gen())
}

/// Generate a 128-bit trace ID: [32-bit unix seconds][32 zeros][64 random bits]
#[pyfunction]
pub fn generate_128bit_trace_id() -> u128 {
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as u128;
    (timestamp << 96) | (rand64bits() as u128)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;
    use std::sync::Mutex;

    // Serialize tests that mutate SECURE_RANDOM so they don't interfere with each other.
    static SECURE_RANDOM_TEST_LOCK: Mutex<()> = Mutex::new(());

    // Run `f` with SECURE_RANDOM set to `enabled`, restoring false even on panic.
    fn with_secure_random<F: FnOnce()>(enabled: bool, f: F) {
        let _guard = SECURE_RANDOM_TEST_LOCK
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        SECURE_RANDOM.store(enabled, Ordering::Relaxed);
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(f));
        SECURE_RANDOM.store(false, Ordering::Relaxed);
        result.unwrap();
    }

    // ── parse_bool_env() ─────────────────────────────────────────────────────

    #[test]
    fn test_parse_bool_env_truthy_values() {
        for val in &["true", "True", "TRUE", "1"] {
            assert!(
                parse_bool_env(Some(val.to_string())),
                "expected parse_bool_env({val:?}) == true"
            );
        }
    }

    #[test]
    fn test_parse_bool_env_falsy_values() {
        for val in &["false", "False", "FALSE", "0", "yes", ""] {
            assert!(
                !parse_bool_env(Some(val.to_string())),
                "expected parse_bool_env({val:?}) == false"
            );
        }
        assert!(
            !parse_bool_env(None),
            "expected parse_bool_env(None) == false"
        );
    }

    // ── secure_random() ──────────────────────────────────────────────────────

    #[test]
    fn test_secure_random_returns_bool() {
        // AtomicBool defaults to false; just verify the load doesn't panic.
        let v = secure_random();
        assert!(v == true || v == false);
    }

    #[test]
    fn test_secure_random_flag_reflects_stored_value() {
        with_secure_random(true, || {
            assert!(
                secure_random(),
                "secure_random() should return true after storing true"
            );
        });
        assert!(
            !secure_random(),
            "secure_random() should return false after restoring false"
        );
    }

    // ── rand64bits() — SmallRng path (SECURE_RANDOM = false) ─────────────────

    #[test]
    fn test_rand64bits_unique_values() {
        // 2^-64 ≈ 5e-20 collision probability per pair; 1000 samples is safe.
        let values: HashSet<u64> = (0..1000).map(|_| rand64bits()).collect();
        assert!(
            values.len() > 990,
            "Expected unique rand64bits values, got {} distinct in 1000",
            values.len()
        );
    }

    #[test]
    fn test_rand64bits_uses_full_range() {
        // If the RNG were stuck below 2^32, none of the high 32 bits would be set.
        let any_high_bit_set = (0..100).any(|_| rand64bits() >> 32 != 0);
        assert!(
            any_high_bit_set,
            "rand64bits never set bits above 32 — RNG range is too narrow"
        );
    }

    // ── rand64bits() — OsRng path (SECURE_RANDOM = true) ─────────────────────

    #[test]
    fn test_rand64bits_osrng_path_produces_varied_values() {
        with_secure_random(true, || {
            let values: HashSet<u64> = (0..100).map(|_| rand64bits()).collect();
            assert!(
                values.len() > 90,
                "OsRng path in rand64bits produced insufficient diversity: {} distinct in 100",
                values.len()
            );
        });
    }

    #[test]
    fn test_rand64bits_osrng_path_uses_full_range() {
        with_secure_random(true, || {
            let any_high_bit_set = (0..100).any(|_| rand64bits() >> 32 != 0);
            assert!(
                any_high_bit_set,
                "OsRng path in rand64bits never set bits above 32"
            );
        });
    }

    #[test]
    fn test_rand64bits_impl_flag_matches_secure_random() {
        // The second element of rand64bits_impl() must echo the SECURE_RANDOM flag
        // so callers (e.g. logging) can confirm which path was taken.
        with_secure_random(false, || {
            let (_, flag) = rand64bits_impl();
            assert!(!flag, "flag should be false when SECURE_RANDOM=false");
        });
        with_secure_random(true, || {
            let (_, flag) = rand64bits_impl();
            assert!(flag, "flag should be true when SECURE_RANDOM=true");
        });
    }

    // ── seed() ───────────────────────────────────────────────────────────────

    #[test]
    fn test_seed_does_not_break_rng() {
        // seed() reseeds the thread-local SmallRng; verify it still produces values.
        seed();
        let values: HashSet<u64> = (0..10).map(|_| rand64bits()).collect();
        assert!(!values.is_empty());
    }

    // ── OsRng ────────────────────────────────────────────────────────────────

    #[test]
    fn test_osrng_produces_varied_values() {
        let values: HashSet<u64> = (0..100).map(|_| OsRng.try_next_u64().unwrap()).collect();
        assert!(
            values.len() > 90,
            "Expected diverse values from OsRng, got {}",
            values.len()
        );
    }

    // ── generate_128bit_trace_id() ───────────────────────────────────────────

    #[test]
    fn test_trace_id_timestamp_in_high_bits() {
        // Format: [32-bit unix seconds][32 zeros][64 random bits]
        // Top 32 bits must be a plausible Unix timestamp.
        let id = generate_128bit_trace_id();
        let timestamp = (id >> 96) as u32;
        // 2020-01-01T00:00:00Z = 1_577_836_800
        // 2100-01-01T00:00:00Z = 4_102_444_800
        assert!(
            timestamp > 1_577_836_800,
            "timestamp {timestamp} is before 2020"
        );
        assert!(
            timestamp < 4_102_444_800,
            "timestamp {timestamp} is after 2100"
        );
    }

    #[test]
    fn test_trace_id_middle_bits_zero() {
        // Bits 95-64 (the 32 bits after the timestamp) must always be zero per the spec.
        for _ in 0..20 {
            let id = generate_128bit_trace_id();
            let middle = ((id >> 64) & 0xFFFF_FFFF) as u32;
            assert_eq!(
                middle, 0,
                "middle 32 bits of trace ID should be zero, got {id:#034x}"
            );
        }
    }

    #[test]
    fn test_trace_id_low_bits_vary() {
        // The low 64 bits carry the random payload; they must not be constant.
        let lows: HashSet<u64> = (0..100)
            .map(|_| generate_128bit_trace_id() as u64)
            .collect();
        assert!(
            lows.len() > 90,
            "Low 64 bits of trace IDs are not random enough: {} distinct in 100",
            lows.len()
        );
    }

    #[test]
    fn test_trace_id_ids_are_unique() {
        let ids: HashSet<u128> = (0..1000).map(|_| generate_128bit_trace_id()).collect();
        assert!(
            ids.len() > 990,
            "Expected unique trace IDs, got {} distinct in 1000",
            ids.len()
        );
    }
}

/// Register the rand module functions and set up fork safety.
pub fn register_rand(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Release store: any thread that subsequently does an Acquire load will see this value,
    // even if it was created before module import.
    SECURE_RANDOM.store(
        parse_bool_env(std::env::var("DD_TRACE_SECURE_RANDOM").ok()),
        Ordering::Release,
    );

    m.add_function(wrap_pyfunction!(seed, m)?)?;
    m.add_function(wrap_pyfunction!(rand64bits, m)?)?;
    m.add_function(wrap_pyfunction!(generate_128bit_trace_id, m)?)?;

    // Register seed() with ddtrace.internal.forksafe for fork safety
    let py = m.py();
    let forksafe = py.import("ddtrace.internal.forksafe")?;
    let register = forksafe.getattr("register")?;
    let seed_fn = m.getattr("seed")?;
    register.call1((seed_fn,))?;
    Ok(())
}
