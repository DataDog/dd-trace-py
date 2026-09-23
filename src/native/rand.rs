use pyo3::{prelude::*, types::PyModule};
use rand::{
    rngs::{OsRng, SmallRng},
    Rng, SeedableRng, TryRngCore,
};
use std::cell::RefCell;
use std::sync::atomic::{AtomicBool, Ordering};
use tracing::warn;

// Set once at module init (Release) when a MicroVM environment is detected; every read is an
// Acquire load so the store is visible to threads that did not participate in module import.
static SECURE_RANDOM: AtomicBool = AtomicBool::new(false);
static OS_RNG_FAILURE_LOGGED: AtomicBool = AtomicBool::new(false);

// Returns true when running inside a Firecracker/Lambda MicroVM where process memory can be
// cloned from a snapshot, making SmallRng state deterministic across resumed instances.
// AWS Lambda sets AWS_LAMBDA_MICROVM_IMAGE_ARN in all MicroVM-based invocations.
fn is_microvm_environment() -> bool {
    std::env::var("AWS_LAMBDA_MICROVM_IMAGE_ARN")
        .map(|v| !v.is_empty())
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
        if let Ok(value) = OsRng.try_next_u64() {
            return value;
        }
        if !OS_RNG_FAILURE_LOGGED.swap(true, Ordering::Relaxed) {
            warn!("OsRng failed in secure random mode; falling back to thread-local SmallRng");
        }
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

    // Serialize tests that mutate env vars to avoid races between parallel test threads.
    static ENV_TEST_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn test_is_microvm_environment() {
        let _guard = ENV_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var(
            "AWS_LAMBDA_MICROVM_IMAGE_ARN",
            "arn:aws:lambda:us-east-1::runtime:python3.12",
        );
        assert!(
            is_microvm_environment(),
            "expected is_microvm_environment() == true when ARN is set"
        );

        std::env::set_var("AWS_LAMBDA_MICROVM_IMAGE_ARN", "");
        assert!(
            !is_microvm_environment(),
            "expected is_microvm_environment() == false when ARN is empty"
        );

        std::env::remove_var("AWS_LAMBDA_MICROVM_IMAGE_ARN");
        assert!(
            !is_microvm_environment(),
            "expected is_microvm_environment() == false when ARN is unset"
        );
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

    #[test]
    fn test_rand64bits_smallrng_path_produces_varied_64bit_values() {
        with_secure_random(false, || {
            let values: HashSet<u64> = (0..1000).map(|_| rand64bits()).collect();
            assert!(
                values.len() > 990,
                "Expected unique rand64bits values, got {} distinct in 1000",
                values.len()
            );
            assert!(
                values.iter().any(|value| value >> 32 != 0),
                "rand64bits never set bits above 32"
            );
        });
    }

    #[test]
    fn test_rand64bits_osrng_path_produces_varied_64bit_values() {
        with_secure_random(true, || {
            let values: HashSet<u64> = (0..100).map(|_| rand64bits()).collect();
            assert!(
                values.len() > 90,
                "OsRng path in rand64bits produced insufficient diversity: {} distinct in 100",
                values.len()
            );
            assert!(
                values.iter().any(|value| value >> 32 != 0),
                "OsRng path in rand64bits never set bits above 32"
            );
        });
    }

    #[test]
    fn test_seed_does_not_break_rng() {
        // seed() reseeds the thread-local SmallRng; verify it still produces values.
        seed();
        let values: HashSet<u64> = (0..10).map(|_| rand64bits()).collect();
        assert!(!values.is_empty());
    }

    #[test]
    fn test_generate_128bit_trace_id_layout_and_random_payload() {
        let mut lows = HashSet::new();
        for _ in 0..100 {
            let id = generate_128bit_trace_id();
            let timestamp = (id >> 96) as u32;
            let middle = ((id >> 64) & 0xFFFF_FFFF) as u32;
            lows.insert(id as u64);

            assert!(
                timestamp > 1_577_836_800,
                "timestamp {timestamp} is before 2020"
            );
            assert!(
                timestamp < 4_102_444_800,
                "timestamp {timestamp} is after 2100"
            );
            assert_eq!(
                middle, 0,
                "middle 32 bits of trace ID should be zero, got {id:#034x}"
            );
        }

        assert!(
            lows.len() > 90,
            "Low 64 bits of trace IDs are not random enough: {} distinct in 100",
            lows.len()
        );
    }
}

/// Register the rand module functions and set up fork safety.
pub fn register_rand(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Release store: any thread that subsequently does an Acquire load will see this value,
    // even if it was created before module import.
    SECURE_RANDOM.store(is_microvm_environment(), Ordering::Release);

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
