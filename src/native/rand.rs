use pyo3::{prelude::*, types::PyModule};
use rand::{rngs::SmallRng, Rng, SeedableRng};
use std::cell::RefCell;

fn create_rng() -> SmallRng {
    let mut source = rand::rng();
    SmallRng::from_rng(&mut source)
}

thread_local! {
    static RNG: RefCell<SmallRng> = RefCell::new(create_rng());
}

/// Reseed the thread-local RNG with fresh entropy from the OS.
/// This is called after fork to ensure child processes have independent RNG state.
#[pyfunction]
pub fn seed() {
    RNG.with(|rng| {
        *rng.borrow_mut() = create_rng();
    });
}

/// Generate a random 64-bit unsigned integer.
#[pyfunction]
pub fn rand64bits() -> u64 {
    RNG.with(|rng| rng.borrow_mut().random())
}

/// Register the rand module functions and set up fork safety.
pub fn register_rand(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(seed, m)?)?;
    m.add_function(wrap_pyfunction!(rand64bits, m)?)?;

    // Register seed() with ddtrace.internal.forksafe for fork safety
    let py = m.py();
    let forksafe = py.import("ddtrace.internal.forksafe")?;
    let register = forksafe.getattr("register")?;
    let seed_fn = m.getattr("seed")?;
    register.call1((seed_fn,))?;
    Ok(())
}
