use pyo3::{prelude::*, types::PyModule};
use rand::{rngs::SmallRng, Rng, SeedableRng};
use std::cell::RefCell;

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
