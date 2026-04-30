use std::time::SystemTime;

use crate::py_string::PyBackedString;
use pyo3::{
    types::PyAnyMethods as _,
    Bound, PyAny,
};

/// Extract PyBackedString from Python object, falling back to empty string on error.
///
/// Used for required properties (like `name`) which must always have a value.
/// Invalid types (int, float, list, etc.) are silently converted to "" rather than raising an error.
#[inline(always)]
pub fn extract_backed_string_or_default(obj: &Bound<'_, PyAny>) -> PyBackedString {
    obj.extract::<PyBackedString>().unwrap_or_default()
}

/// Extract PyBackedString from Python object, falling back to None on error.
///
/// Used for optional properties (like `service`) which can be None.
/// Invalid types (int, float, list, etc.) are silently converted to None rather than raising an error.
#[inline(always)]
pub fn extract_backed_string_or_none(obj: &Bound<'_, PyAny>) -> PyBackedString {
    let py = obj.py();
    obj.extract::<PyBackedString>()
        .unwrap_or_else(|_| PyBackedString::py_none(py))
}

/// Extract i64 from Python object, falling back to 0 on error.
/// Accepts int or float (truncated).
#[inline(always)]
pub fn extract_i64_or_default(obj: &Bound<'_, PyAny>) -> i64 {
    obj.extract::<i64>()
        .or_else(|_| obj.extract::<f64>().map(|f| f as i64))
        .unwrap_or(0)
}

/// Extract i32 from Python object, falling back to 0 on error.
/// Note: Python bool subclasses int, so bools extract as 0/1 automatically.
#[inline(always)]
pub fn extract_i32_or_default(obj: &Bound<'_, PyAny>) -> i32 {
    obj.extract::<i32>().unwrap_or(0)
}


/// Get wall clock time in nanoseconds since Unix epoch.
/// Uses SystemTime for wall clock (matches Python's time.time_ns()).
#[inline(always)]
pub fn wall_clock_ns() -> i64 {
    SystemTime::UNIX_EPOCH
        .elapsed()
        .map(|d| d.as_nanos() as i64)
        .unwrap_or(0)
}

/// Extract a `time_unix_nano` value from an optional Python object.
///
/// - `None` (omitted) → current wall clock time
/// - Non-negative int → use as-is
/// - Negative int → 0 (graceful fallback; avoids TypeError from bad propagation headers)
/// - Invalid type → 0
#[inline(always)]
pub fn extract_time_unix_nano(obj: Option<&Bound<'_, PyAny>>) -> u64 {
    match obj {
        None => wall_clock_ns() as u64,
        Some(o) if o.is_none() => wall_clock_ns() as u64,
        Some(o) => o
            .extract::<u64>()
            .or_else(|_| o.extract::<i64>().map(|v| if v < 0 { 0 } else { v as u64 }))
            .unwrap_or(0),
    }
}
