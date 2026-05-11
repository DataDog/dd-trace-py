use pyo3::{prelude::*, types::PyModule};
use rand::{rngs::{OsRng, SmallRng}, Rng, TryRngCore, SeedableRng};
use std::cell::RefCell;
use std::sync::OnceLock;

static SECURE_RANDOM: OnceLock<bool> = OnceLock::new();

fn secure_random() -> bool {
    *SECURE_RANDOM.get_or_init(|| {
        std::env::var("DD_TRACE_SECURE_RANDOM").as_deref() == Ok("true")
    })
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

    // ── secure_random() ──────────────────────────────────────────────────────

    #[test]
    fn test_secure_random_returns_bool() {
        // OnceLock is initialised once per process; just verify no panic.
        let v = secure_random();
        assert!(v == true || v == false);
    }

    // ── rand64bits() ─────────────────────────────────────────────────────────

    #[test]
    fn test_rand64bits_unique_values() {
        // 2^-64 ≈ 5e-20 collision probability per pair; 1000 samples is safe.
        let values: HashSet<u64> = (0..1000).map(|_| rand64bits()).collect();
        assert!(values.len() > 990, "Expected unique rand64bits values, got {} distinct in 1000", values.len());
    }

    #[test]
    fn test_rand64bits_uses_full_range() {
        // If the RNG were stuck below 2^32, none of the high 32 bits would be set.
        let any_high_bit_set = (0..100).any(|_| rand64bits() >> 32 != 0);
        assert!(any_high_bit_set, "rand64bits never set bits above 32 — RNG range is too narrow");
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
        assert!(values.len() > 90, "Expected diverse values from OsRng, got {}", values.len());
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
        assert!(timestamp > 1_577_836_800, "timestamp {timestamp} is before 2020");
        assert!(timestamp < 4_102_444_800, "timestamp {timestamp} is after 2100");
    }

    #[test]
    fn test_trace_id_middle_bits_zero() {
        // Bits 95-64 (the 32 bits after the timestamp) must always be zero per the spec.
        for _ in 0..20 {
            let id = generate_128bit_trace_id();
            let middle = ((id >> 64) & 0xFFFF_FFFF) as u32;
            assert_eq!(middle, 0, "middle 32 bits of trace ID should be zero, got {id:#034x}");
        }
    }

    #[test]
    fn test_trace_id_low_bits_vary() {
        // The low 64 bits carry the random payload; they must not be constant.
        let lows: HashSet<u64> = (0..100).map(|_| generate_128bit_trace_id() as u64).collect();
        assert!(lows.len() > 90, "Low 64 bits of trace IDs are not random enough: {} distinct in 100", lows.len());
    }

    #[test]
    fn test_trace_id_ids_are_unique() {
        let ids: HashSet<u128> = (0..1000).map(|_| generate_128bit_trace_id()).collect();
        assert!(ids.len() > 990, "Expected unique trace IDs, got {} distinct in 1000", ids.len());
    }
}

/// Register the rand module functions and set up fork safety.
pub fn register_rand(m: &Bound<'_, PyModule>) -> PyResult<()> {
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
