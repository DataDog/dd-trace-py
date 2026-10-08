// LLM usage and cost metrics (libdd-ai-usage).
//
// An integration describes each provider call or gateway request as a plain dict.
// libdd-ai-usage checks it, normalizes its usage and returns the metric points;
// this module keeps the points of one export window and encodes them for OTLP or
// DogStatsD. Python only collects fields and sends the bytes.

use pyo3::pymodule;

#[pymodule]
pub mod ai_usage {
    use std::collections::{BTreeMap, BTreeSet};
    use std::sync::Mutex;

    use libdd_ai_usage::{
        project_result, with_deployment_attributes, Json, JsonObject, MetricBatch, MetricError,
        OtlpScope, OtlpWindow, Profile,
    };
    use pyo3::exceptions::{PyRuntimeError, PyTypeError, PyValueError};
    use pyo3::prelude::*;
    use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};

    /// Nesting deeper than this is refused, as the crate's own JSON reader does.
    const MAX_DEPTH: usize = 128;

    /// Convert a Python value to the crate's JSON value. Only the JSON types are
    /// accepted: `bool` is never read as a number, and no `__int__`, `__float__`
    /// or `__str__` coercion happens. An int too large for a double is an
    /// infinity, which every rule treats as above every bound; NaN, which JSON
    /// cannot hold, becomes a value of the wrong type.
    fn to_json(value: &Bound<'_, PyAny>, depth: usize) -> PyResult<Json> {
        if depth > MAX_DEPTH {
            return Err(PyValueError::new_err("observation is nested too deeply"));
        }
        if value.is_none() {
            return Ok(Json::Null);
        }
        if let Ok(flag) = value.cast_exact::<PyBool>() {
            return Ok(Json::Bool(flag.is_true()));
        }
        if value.is_exact_instance_of::<PyInt>() {
            return Ok(Json::Number(match value.extract::<f64>() {
                Ok(number) => number,
                // Too large for a double.
                Err(_) if value.lt(0)? => f64::NEG_INFINITY,
                Err(_) => f64::INFINITY,
            }));
        }
        if let Ok(number) = value.cast_exact::<PyFloat>() {
            let number = number.value();
            return Ok(if number.is_nan() {
                Json::String("NaN".to_owned())
            } else {
                Json::Number(number)
            });
        }
        if let Ok(text) = value.cast_exact::<PyString>() {
            return Ok(Json::String(text.to_str()?.to_owned()));
        }
        if let Ok(items) = value.cast_exact::<PyList>() {
            return items
                .iter()
                .map(|item| to_json(&item, depth + 1))
                .collect::<PyResult<Vec<Json>>>()
                .map(Json::Array);
        }
        if let Ok(items) = value.cast_exact::<PyTuple>() {
            return items
                .iter()
                .map(|item| to_json(&item, depth + 1))
                .collect::<PyResult<Vec<Json>>>()
                .map(Json::Array);
        }
        if let Ok(members) = value.cast_exact::<PyDict>() {
            let mut object = JsonObject::new();
            for (key, member) in members.iter() {
                let key = key
                    .cast_exact::<PyString>()
                    .map_err(|_| PyTypeError::new_err("observation keys must be str"))?;
                object.insert(key.to_str()?.to_owned(), to_json(&member, depth + 1)?);
            }
            return Ok(Json::Object(object));
        }
        Err(PyTypeError::new_err(format!(
            "unsupported observation value of type {}",
            value.get_type().name()?
        )))
    }

    /// A rejected observation: `ValueError(error_code, message)`.
    fn rejected(error: MetricError) -> PyErr {
        PyValueError::new_err((error.code().as_str(), error.message().to_owned()))
    }

    fn profile(id: &str) -> PyResult<Profile> {
        Profile::from_id(id).ok_or_else(|| PyValueError::new_err(("profile_invalid", id.to_owned())))
    }

    /// The metric points of one export window.
    ///
    /// `record` projects an observation and adds its points; `take_otlp` and
    /// `take_dogstatsd` encode the points recorded so far and start a new window.
    /// Safe to share between threads.
    #[pyclass(frozen)]
    struct UsageMetrics {
        batch: Mutex<MetricBatch>,
        /// The metrics to keep; every metric of a profile when `None`.
        metrics: Option<BTreeSet<String>>,
    }

    impl UsageMetrics {
        fn take(&self) -> PyResult<MetricBatch> {
            let mut batch = self
                .batch
                .lock()
                .map_err(|_| PyRuntimeError::new_err("usage metrics lock poisoned"))?;
            Ok(std::mem::take(&mut *batch))
        }
    }

    #[pymethods]
    impl UsageMetrics {
        /// `metrics` lists the metric names to keep; by default every metric
        /// a profile defines is kept.
        #[new]
        #[pyo3(signature = (metrics=None))]
        fn new(metrics: Option<Vec<String>>) -> Self {
            Self {
                batch: Mutex::new(MetricBatch::new()),
                metrics: metrics.map(|names| names.into_iter().collect()),
            }
        }

        /// Project one observation under `profile` (a versioned profile id such
        /// as `gen_ai.client.provider_attempt@0.1.0`) and add its points, with
        /// the given deployment attributes. Returns the issue codes raised beside
        /// the points. Raises `ValueError(error_code, message)` when the
        /// observation is rejected; nothing is recorded then.
        #[pyo3(signature = (profile_id, observation, deployment_attributes=None))]
        fn record(
            &self,
            py: Python<'_>,
            profile_id: &str,
            observation: &Bound<'_, PyDict>,
            deployment_attributes: Option<BTreeMap<String, String>>,
        ) -> PyResult<Vec<String>> {
            let profile = profile(profile_id)?;
            let observation = to_json(observation.as_any(), 0)?;
            py.detach(|| {
                let mut projection = project_result(profile, &observation).map_err(rejected)?;
                if let Some(attributes) = deployment_attributes {
                    projection =
                        with_deployment_attributes(projection, &attributes).map_err(rejected)?;
                }
                let issues = projection
                    .issues
                    .iter()
                    .map(|issue| issue.as_str().to_owned())
                    .collect();
                if let Some(metrics) = &self.metrics {
                    projection.points.retain(|point| metrics.contains(&point.name));
                }
                self.batch
                    .lock()
                    .map_err(|_| PyRuntimeError::new_err("usage metrics lock poisoned"))?
                    .add(profile, &projection)
                    .map_err(rejected)?;
                Ok(issues)
            })
        }

        fn is_empty(&self) -> PyResult<bool> {
            Ok(self
                .batch
                .lock()
                .map_err(|_| PyRuntimeError::new_err("usage metrics lock poisoned"))?
                .is_empty())
        }

        /// Take the points recorded so far as an OTLP
        /// `ExportMetricsServiceRequest` in protobuf, or `None` when there are
        /// none. Delta points cover `start_time_unix_nano` to `time_unix_nano`.
        fn take_otlp<'py>(
            &self,
            py: Python<'py>,
            scope_name: &str,
            scope_version: &str,
            start_time_unix_nano: u64,
            time_unix_nano: u64,
        ) -> PyResult<Option<Bound<'py, PyBytes>>> {
            let batch = self.take()?;
            if batch.is_empty() {
                return Ok(None);
            }
            let payload = py.detach(|| {
                batch.encode_otlp(
                    OtlpScope {
                        name: scope_name,
                        version: scope_version,
                    },
                    OtlpWindow {
                        start_time_unix_nano,
                        time_unix_nano,
                    },
                )
            });
            Ok(Some(PyBytes::new(py, &payload)))
        }

        /// Take the points recorded so far as DogStatsD lines, one per series
        /// point, without newlines.
        fn take_dogstatsd(&self, py: Python<'_>) -> PyResult<Vec<String>> {
            let batch = self.take()?;
            py.detach(|| batch.dogstatsd_lines()).map_err(rejected)
        }
    }
}
