//! Python conversion only; accounting policy lives in libdd-ai-usage.

use std::collections::BTreeMap;

use libdd_ai_usage::{Details, InputBasis, Measurement, ToolCount, UsageInput, Value};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyFloat, PyInt, PyString};

fn scalar(value: Option<&Bound<'_, PyAny>>) -> Value {
    let Some(value) = value.filter(|value| !value.is_none()) else {
        return Value::Missing;
    };
    // Do not coerce booleans, strings, subclasses, or arbitrary __int__/__float__ hooks.
    if value.cast_exact::<PyInt>().is_ok() {
        value
            .extract::<u64>()
            .map(Value::Integer)
            .unwrap_or(Value::Invalid)
    } else if value.cast_exact::<PyFloat>().is_ok() {
        value
            .extract::<f64>()
            .map(Value::Fraction)
            .unwrap_or(Value::Invalid)
    } else {
        Value::Invalid
    }
}

fn field(raw: &Bound<'_, PyDict>, name: &str) -> PyResult<Value> {
    Ok(scalar(raw.get_item(name)?.as_ref()))
}

fn details(raw: &Bound<'_, PyDict>, key: &str) -> PyResult<Details> {
    let Some(value) = raw.get_item(key)? else {
        return Ok(Details::default());
    };
    let raw = value.cast::<PyDict>()?;
    Ok(Details {
        text_tokens: field(raw, "text_tokens")?,
        audio_tokens: field(raw, "audio_tokens")?,
        image_tokens: field(raw, "image_tokens")?,
        video_tokens: field(raw, "video_tokens")?,
        cached_tokens: field(raw, "cached_tokens")?,
        reasoning_tokens: field(raw, "reasoning_tokens")?,
        tool_use_tokens: field(raw, "tool_use_tokens")?,
        character_count: field(raw, "character_count")?,
        image_count: field(raw, "image_count")?,
        accepted_prediction_tokens: field(raw, "accepted_prediction_tokens")?,
        rejected_prediction_tokens: field(raw, "rejected_prediction_tokens")?,
        audio_length_seconds: field(raw, "audio_length_seconds")?,
        video_length_seconds: field(raw, "video_length_seconds")?,
    })
}

fn tool(raw: &Bound<'_, PyDict>, name: &str) -> PyResult<ToolCount> {
    Ok(ToolCount {
        native: field(raw, name)?,
        normalized: field(raw, &format!("normalized_{name}"))?,
    })
}

fn input(raw: &Bound<'_, PyDict>) -> PyResult<UsageInput> {
    Ok(UsageInput {
        input: field(raw, "input")?,
        output: field(raw, "output")?,
        input_basis: if raw
            .get_item("input_excludes_cache")?
            .is_some_and(|v| v.extract::<bool>().unwrap_or(false))
        {
            InputBasis::ExcludesCache
        } else {
            InputBasis::IncludesCache
        },
        embedding: raw
            .get_item("embedding")?
            .is_some_and(|v| v.extract::<bool>().unwrap_or(false)),
        cache_read: field(raw, "cache_read")?,
        cache_write: field(raw, "cache_write")?,
        cache_write_5m: field(raw, "cache_write_5m")?,
        cache_write_1h: field(raw, "cache_write_1h")?,
        input_details: details(raw, "input_details")?,
        output_details: details(raw, "output_details")?,
        web_search_requests: tool(raw, "web_search_requests")?,
        tool_search_requests: tool(raw, "tool_search_requests")?,
        browser_open_requests: tool(raw, "browser_open_requests")?,
        google_maps_grounding_requests: tool(raw, "google_maps_grounding_requests")?,
    })
}

type Normalized = (BTreeMap<&'static str, u64>, Py<PyDict>, Vec<&'static str>);

#[pyfunction]
pub fn _normalize_ai_usage(
    py: Python<'_>,
    raw: Option<&Bound<'_, PyDict>>,
) -> PyResult<Normalized> {
    let input = raw.map(input).transpose()?;
    let result = libdd_ai_usage::normalize(input.as_ref());
    let observations = PyDict::new(py);
    for (name, value) in result.observations {
        match value {
            Measurement::Count(value) => observations.set_item(name, value)?,
            Measurement::Duration(value) => observations.set_item(name, value)?,
        }
    }
    Ok((
        result.quantities,
        observations.unbind(),
        result.issues.iter().map(|issue| issue.as_str()).collect(),
    ))
}

#[pyfunction]
pub fn _ai_usage_context_bucket(tokens: &Bound<'_, PyAny>, reliable: bool) -> String {
    libdd_ai_usage::context_tokens_bucket(scalar(Some(tokens)), reliable)
}

#[pyfunction]
#[pyo3(signature = (value, max_length=256))]
pub fn _ai_usage_label(value: &Bound<'_, PyAny>, max_length: i64) -> Option<String> {
    let value = value.cast::<PyString>().ok()?.to_str().ok()?;
    let max_length = usize::try_from(max_length).ok()?;
    libdd_ai_usage::valid_label(value, max_length).then(|| value.to_owned())
}
