mod ddsketch;
mod rate_limiter;

use pyo3::prelude::*;

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<rate_limiter::RateLimiterPy>()?;
    m.add_class::<ddsketch::DDSketchPy>()?;
    Ok(())
}
