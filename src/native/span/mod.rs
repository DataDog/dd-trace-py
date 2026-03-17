use pyo3::types::PyModuleMethods as _;

mod span_data;
mod span_event;
mod span_link;
pub mod utils;

pub use span_data::SpanData;
pub use span_event::SpanEventData;
pub use span_link::SpanLinkData;

pub fn register_native_span(m: &pyo3::Bound<'_, pyo3::types::PyModule>) -> pyo3::PyResult<()> {
    m.add_class::<SpanLinkData>()?;
    m.add_class::<SpanEventData>()?;
    m.add_class::<SpanData>()?;
    Ok(())
}
