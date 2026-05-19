use pyo3::types::PyModuleMethods as _;

pub mod attributes;
mod span_data;
mod span_event;
mod span_link;
pub mod utils;

pub use span_data::SpanData;
pub use span_event::SpanEvent;
pub use span_link::SpanLink;

pub fn register_native_span(m: &pyo3::Bound<'_, pyo3::types::PyModule>) -> pyo3::PyResult<()> {
    m.add_class::<SpanLink>()?;
    m.add_class::<SpanEvent>()?;
    m.add_class::<SpanData>()?;
    Ok(())
}
