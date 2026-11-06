// LLM usage and cost metrics (libdd-ai-usage).
//
// A thin binding over the crate's document-style API: an integration passes
// plain dicts and bytes, the crate reads provider responses and streams, checks
// and projects observations, and encodes a batch as OTLP or DogStatsD.

use pyo3::pymodule;

#[pymodule]
pub mod ai_usage {
    use std::collections::{BTreeMap, BTreeSet};
    use std::sync::Mutex;

    use libdd_ai_usage::{
        project_result, with_deployment_attributes, Issues, Json, JsonObject, MetricBatch,
        MetricError, MetricPoint, OtlpBinding, OtlpScope, OtlpWindow, Profile, Projection,
        ProviderResponse, StreamOutcome, UsageFormat, UsageStream,
    };
    use pyo3::exceptions::{PyRuntimeError, PyTypeError, PyValueError};
    use pyo3::prelude::*;
    use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};

    /// A Python value as the crate's JSON. Only JSON types are accepted: no
    /// coercion, `bool` is never a number, and NaN becomes a value of the wrong
    /// type. Nesting deeper than the crate's own reader allows is refused.
    fn to_json(value: &Bound<'_, PyAny>, depth: usize) -> PyResult<Json> {
        if depth > 128 {
            return Err(PyValueError::new_err("observation is nested too deeply"));
        }
        Ok(if value.is_none() {
            Json::Null
        } else if let Ok(flag) = value.cast_exact::<PyBool>() {
            Json::Bool(flag.is_true())
        } else if value.is_exact_instance_of::<PyInt>() {
            Json::Number(value.extract::<f64>().unwrap_or(if value.lt(0)? {
                f64::NEG_INFINITY
            } else {
                f64::INFINITY
            }))
        } else if let Ok(number) = value.cast_exact::<PyFloat>() {
            match number.value() {
                n if n.is_nan() => Json::String("NaN".to_owned()),
                n => Json::Number(n),
            }
        } else if let Ok(text) = value.cast_exact::<PyString>() {
            Json::String(text.to_str()?.to_owned())
        } else if value.is_exact_instance_of::<PyList>() || value.is_exact_instance_of::<PyTuple>()
        {
            Json::Array(
                value
                    .try_iter()?
                    .map(|item| to_json(&item?, depth + 1))
                    .collect::<PyResult<_>>()?,
            )
        } else if let Ok(members) = value.cast_exact::<PyDict>() {
            let mut object = JsonObject::new();
            for (key, member) in members.iter() {
                let key = key
                    .cast_exact::<PyString>()
                    .map_err(|_| PyTypeError::new_err("observation keys must be str"))?;
                object.insert(key.to_str()?.to_owned(), to_json(&member, depth + 1)?);
            }
            Json::Object(object)
        } else {
            return Err(PyTypeError::new_err(format!(
                "unsupported observation value of type {}",
                value.get_type().name()?
            )));
        })
    }

    fn rejected(error: MetricError) -> PyErr {
        PyValueError::new_err((error.code().as_str(), error.message().to_owned()))
    }

    fn named<T>(found: Option<T>, what: &str, name: &str) -> PyResult<T> {
        found.ok_or_else(|| PyValueError::new_err((format!("{what}_invalid"), name.to_owned())))
    }

    fn bytes_of<'a>(value: &'a Bound<'_, PyAny>) -> PyResult<Option<&'a [u8]>> {
        if let Ok(bytes) = value.cast::<PyBytes>() {
            return Ok(Some(bytes.as_bytes()));
        }
        Ok(match value.cast::<PyString>() {
            Ok(text) => Some(text.to_str()?.as_bytes()),
            Err(_) => None,
        })
    }

    /// `{"response": {usage, response_model, service_tier, ...}, "outcome", "error_name"}` as JSON.
    fn report(
        response: &ProviderResponse,
        outcome: StreamOutcome,
        error: Option<String>,
    ) -> String {
        serde_json::json!({"response": response, "outcome": outcome.as_str(), "error_name": error})
            .to_string()
    }

    /// What a provider response reported: a decoded payload (dict or list of
    /// events), or a whole body as str or bytes (JSON or server-sent events).
    #[pyfunction]
    #[pyo3(signature = (usage_format, payload, embeddings=false))]
    fn parse(
        py: Python<'_>,
        usage_format: &str,
        payload: &Bound<'_, PyAny>,
        embeddings: bool,
    ) -> PyResult<String> {
        let format = named(
            UsageFormat::from_name(usage_format),
            "usage_format",
            usage_format,
        )?;
        if let Some(body) = bytes_of(payload)? {
            let (response, outcome, error) =
                py.detach(|| libdd_ai_usage::parse_provider_body(format, body, embeddings));
            return Ok(report(&response, outcome, error));
        }
        let response =
            libdd_ai_usage::parse_provider_response(format, &to_json(payload, 0)?, embeddings);
        Ok(report(&response, StreamOutcome::Open, None))
    }

    /// What a stream reported, from its raw lines or events (str or bytes, each
    /// ending a line) in order. Other items are not stream data and are skipped.
    #[pyfunction]
    #[pyo3(signature = (usage_format, chunks, embeddings=false))]
    fn parse_stream(
        usage_format: &str,
        chunks: &Bound<'_, PyAny>,
        embeddings: bool,
    ) -> PyResult<String> {
        let mut stream = UsageStream::new(
            named(
                UsageFormat::from_name(usage_format),
                "usage_format",
                usage_format,
            )?,
            embeddings,
        );
        for chunk in chunks.try_iter()? {
            if let Some(chunk) = bytes_of(&chunk?)? {
                stream.feed(chunk);
                stream.feed(b"\n");
            }
        }
        let (response, outcome, error) = stream.finish();
        Ok(report(&response, outcome, error))
    }

    #[pyfunction]
    fn provider_name(name: &str) -> Option<String> {
        libdd_ai_usage::gen_ai_provider_name(name)
    }

    /// The `error.type` of a failure from its name and HTTP status; `failed`
    /// makes one nothing names `api_error`.
    // TODO: use libdatadog#2827's failure fallback once it is published.
    #[pyfunction]
    #[pyo3(signature = (name=None, status=None, failed=false))]
    fn error_type(name: Option<&str>, status: Option<i64>, failed: bool) -> Option<&'static str> {
        let status = status.and_then(|status| u16::try_from(status).ok());
        libdd_ai_usage::classify_error(name, status)
            .or(failed.then_some(libdd_ai_usage::ERROR_TYPE_API))
    }

    #[pyfunction]
    fn is_service_tier(value: &str) -> bool {
        libdd_ai_usage::is_service_tier(value)
    }

    /// The account and project a provider's response names: from its headers,
    /// read by `header(name)`, and for `aws.bedrock` from a model ARN.
    #[pyfunction]
    fn provider_ids(
        provider_name: &str,
        header: &Bound<'_, PyAny>,
        models: Vec<String>,
    ) -> PyResult<BTreeMap<String, String>> {
        let mut headers = BTreeMap::new();
        for (provider, _, name) in libdd_ai_usage::PROVIDER_ID_HEADERS {
            if provider == provider_name {
                if let Some(value) = header.call1((name,))?.extract::<Option<String>>()? {
                    headers.insert(name, value);
                }
            }
        }
        let mut ids = libdd_ai_usage::provider_id_attributes(provider_name, |name| {
            headers.get(name).map(String::as_str)
        });
        if provider_name == "aws.bedrock" {
            if let Some(account) = models
                .iter()
                .find_map(|model| libdd_ai_usage::bedrock_arn_account(model))
            {
                ids.entry("trajectory.request.provider_account_id".to_owned())
                    .or_insert_with(|| account.to_owned());
            }
        }
        Ok(ids.into_iter().collect())
    }

    /// DogStatsD lines packed into datagrams of at most `max_payload` bytes,
    /// and how many lines were dropped for being longer.
    // TODO: use libdatadog#2827's datagram repacking once it is published.
    #[pyfunction]
    fn pack_dogstatsd(lines: Vec<String>, max_payload: usize) -> (Vec<String>, usize) {
        libdd_ai_usage::pack_dogstatsd_counted(&lines, &[] as &[&str], max_payload)
    }

    /// The metric points of one export window. Safe to share between threads.
    #[pyclass(frozen)]
    struct UsageMetrics {
        batch: Mutex<MetricBatch>,
        metrics: Option<BTreeSet<String>>,
        attributes: BTreeMap<String, String>,
    }

    impl UsageMetrics {
        fn take(&self) -> PyResult<MetricBatch> {
            Ok(std::mem::take(&mut *self.batch.lock().map_err(|_| {
                PyRuntimeError::new_err("usage metrics lock poisoned")
            })?))
        }
    }

    #[pymethods]
    impl UsageMetrics {
        /// `metrics` lists the metric names kept (every one by default); a name
        /// no profile defines raises `ValueError("metric_unknown", name)`.
        /// `deployment_attributes` go on every observation, under its own.
        #[new]
        #[pyo3(signature = (metrics=None, deployment_attributes=None))]
        fn new(
            metrics: Option<Vec<String>>,
            deployment_attributes: Option<BTreeMap<String, String>>,
        ) -> PyResult<Self> {
            let known: BTreeSet<&str> = Profile::ALL
                .iter()
                .flat_map(|p| p.metric_names().iter().copied())
                .collect();
            if let Some(unknown) = metrics
                .iter()
                .flatten()
                .find(|name| !known.contains(name.as_str()))
            {
                return Err(PyValueError::new_err(("metric_unknown", unknown.clone())));
            }
            Ok(Self {
                batch: Mutex::new(MetricBatch::new()),
                metrics: metrics.map(|names| names.into_iter().collect()),
                attributes: deployment_attributes.unwrap_or_default(),
            })
        }

        /// Project one observation under each of `profile_ids` and add them
        /// all, or none. Returns each profile's issue codes. A rejection raises
        /// `ValueError(error_code, message, profile_id)`.
        #[pyo3(signature = (profile_ids, observation, deployment_attributes=None))]
        fn add(
            &self,
            py: Python<'_>,
            profile_ids: Vec<String>,
            observation: &Bound<'_, PyDict>,
            deployment_attributes: Option<BTreeMap<String, String>>,
        ) -> PyResult<Vec<Vec<String>>> {
            let profiles = profile_ids
                .iter()
                .map(|id| named(Profile::from_id(id), "profile", id))
                .collect::<PyResult<Vec<_>>>()?;
            let observation = to_json(observation.as_any(), 0)?;
            let mut attributes = self.attributes.clone();
            attributes.extend(deployment_attributes.unwrap_or_default());
            py.detach(|| {
                let mut projections = Vec::with_capacity(profiles.len());
                let mut issues = Vec::with_capacity(profiles.len());
                for profile in profiles {
                    let fail = |e: MetricError| {
                        PyValueError::new_err((
                            e.code().as_str(),
                            e.message().to_owned(),
                            profile.id(),
                        ))
                    };
                    let mut projection = project_result(profile, &observation).map_err(fail)?;
                    if !attributes.is_empty() {
                        projection =
                            with_deployment_attributes(projection, &attributes).map_err(fail)?;
                    }
                    issues.push(
                        projection
                            .issues
                            .iter()
                            .map(|issue| issue.as_str().to_owned())
                            .collect(),
                    );
                    if let Some(metrics) = &self.metrics {
                        projection
                            .points
                            .retain(|point| metrics.contains(&point.name));
                    }
                    projections.push((profile, projection));
                }
                self.batch
                    .lock()
                    .map_err(|_| PyRuntimeError::new_err("usage metrics lock poisoned"))?
                    .try_add_all(projections)
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

        /// Take the points as OTLP `ExportMetricsServiceRequest`s of at most
        /// `max_bytes` each, split by series; `[]` when there are none.
        /// `datadog` selects the binding for a Datadog intake.
        #[pyo3(signature = (scope_name, scope_version, start_time_unix_nano, time_unix_nano, max_bytes, datadog=false))]
        fn take_otlp<'py>(
            &self,
            py: Python<'py>,
            scope_name: &str,
            scope_version: &str,
            start_time_unix_nano: u64,
            time_unix_nano: u64,
            max_bytes: usize,
            datadog: bool,
        ) -> PyResult<Vec<Bound<'py, PyBytes>>> {
            let batch = self.take()?;
            if batch.is_empty() {
                return Ok(Vec::new());
            }
            let binding = if datadog {
                OtlpBinding::Datadog
            } else {
                OtlpBinding::OpenTelemetry
            };
            let scope = OtlpScope {
                name: scope_name,
                version: scope_version,
            };
            let window = OtlpWindow {
                start_time_unix_nano,
                time_unix_nano,
            };
            let encode = |batch: &MetricBatch| batch.encode_otlp_with(scope, window, binding);
            let mut parts = Vec::new();
            py.detach(|| split_otlp(series_of(&batch), &encode, max_bytes, &mut parts))
                .map_err(rejected)?;
            Ok(parts.iter().map(|part| PyBytes::new(py, part)).collect())
        }

        /// Take the points as DogStatsD datagrams of at most `max_payload`
        /// bytes, NUL-separated, and how many lines were dropped for being longer.
        fn take_dogstatsd<'py>(
            &self,
            py: Python<'py>,
            max_payload: usize,
        ) -> PyResult<(Bound<'py, PyBytes>, usize)> {
            let batch = self.take()?;
            let lines = py.detach(|| batch.dogstatsd_lines()).map_err(rejected)?;
            let (text, dropped) = libdd_ai_usage::pack_dogstatsd_joined(
                lines.iter().map(String::as_str),
                &[] as &[&str],
                max_payload,
                '\0',
            );
            Ok((PyBytes::new(py, text.as_bytes()), dropped))
        }
    }

    // TODO: use libdatadog#2827's size-bounded OTLP encoding once it is published.
    type Series = (libdd_ai_usage::Attributes, Profile, Vec<MetricPoint>);

    /// A batch's series in order: a Counter's sum, or every sample of a Histogram.
    fn series_of(batch: &MetricBatch) -> Vec<Series> {
        let mut series: Vec<Series> = Vec::new();
        for (resource, profile, points) in batch.points() {
            let start = series.len();
            for point in points {
                let fresh = series.len() == start;
                match series.last_mut() {
                    Some(last)
                        if !fresh
                            && last.2[0].name == point.name
                            && last.2[0].instrument == point.instrument
                            && last.2[0].attributes == point.attributes =>
                    {
                        last.2.push(point)
                    }
                    _ => series.push((resource.clone(), profile, vec![point])),
                }
            }
        }
        series
    }

    /// Encode `series` into requests of at most `max_bytes`, halving any that
    /// does not fit. One series is never split, so it can exceed `max_bytes`.
    fn split_otlp(
        series: Vec<Series>,
        encode: &dyn Fn(&MetricBatch) -> Vec<u8>,
        max_bytes: usize,
        parts: &mut Vec<Vec<u8>>,
    ) -> Result<(), MetricError> {
        let count = series.len();
        let mut batch = MetricBatch::new();
        for (resource, profile, points) in &series {
            batch.add(
                *profile,
                &Projection {
                    points: points.clone(),
                    issues: Issues::new(),
                    resource: resource.clone(),
                },
            )?;
        }
        let payload = encode(&batch);
        if payload.len() <= max_bytes || count <= 1 {
            parts.push(payload);
            return Ok(());
        }
        let mut series = series;
        let second = series.split_off(count / 2);
        split_otlp(series, encode, max_bytes, parts)?;
        split_otlp(second, encode, max_bytes, parts)
    }
}
