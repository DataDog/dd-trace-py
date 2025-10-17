//! A random number generator that is reseeded on python forks
//! This rng is using rand::SmallRng underneath to be able to generate
//! trace ids and span ids fast, but it is not suitable for cryptographic 
//! use cases or uses cases requiring a lot of entropy

use std::cell::RefCell;

use pyo3::{
    pyclass, pyfunction,
    types::{PyAnyMethods, PyModule},
    wrap_pyfunction, PyResult,
};
use rand::{
    rngs::{SmallRng, StdRng},
    RngCore, SeedableRng,
};

#[pyclass]
struct Rng(SmallRng);

impl Rng {
    fn new() -> Self {
        Self(SmallRng::from_rng(&mut StdRng::from_os_rng()))
    }
}

thread_local! {
    static RNG: RefCell<Rng> = RefCell::new(Rng::new());
}

#[pyfunction]
fn reseed<'py>() {
    RNG.with_borrow_mut(|rng| {
        let _ = std::mem::replace(rng, Rng::new());
    })
}

pub fn rand64bits() -> u64 {
    RNG.with_borrow_mut(|rng| rng.0.next_u64())
}

pub fn rand128bits() -> u128 {
    let mut value = [0; 16];
    RNG.with_borrow_mut(|rng| {
        rng.0.fill_bytes(&mut value);
    });
    u128::from_ne_bytes(value)
}

pub fn register_rand(m: &pyo3::Bound<'_, PyModule>) -> PyResult<()> {
    let reseed_pyf = wrap_pyfunction!(reseed, m.py())?;
    // Register the reseed function with ddtrace forking machinery
    m.py()
        .import("ddtrace.internal.forksafe")?
        .getattr("register")?
        .call1((reseed_pyf,))?;

    Ok(())
}
