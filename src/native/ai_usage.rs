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
        project_result, with_deployment_attributes, Attributes, Instrument, Issues, Json,
        JsonObject, MetricBatch, MetricError, MetricPoint, OtlpBinding, OtlpScope, OtlpWindow,
        Profile, Projection,
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

    // What a provider reported, read by the crate's shared rules.

    fn format_named(name: &str) -> PyResult<libdd_ai_usage::UsageFormat> {
        libdd_ai_usage::UsageFormat::from_name(name)
            .ok_or_else(|| PyValueError::new_err(format!("unknown usage format: {name}")))
    }

    fn usage_dict<'py>(
        py: Python<'py>,
        usage: &libdd_ai_usage::ProviderUsage,
    ) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        let counts = [
            ("input_tokens", usage.input_tokens),
            ("output_tokens", usage.output_tokens),
            ("cache_read_input_tokens", usage.cache_read_input_tokens),
            ("cache_write_input_tokens", usage.cache_write_input_tokens),
            (
                "cache_write_5m_input_tokens",
                usage.cache_write_5m_input_tokens,
            ),
            (
                "cache_write_1h_input_tokens",
                usage.cache_write_1h_input_tokens,
            ),
            ("reasoning_output_tokens", usage.reasoning_output_tokens),
        ];
        for (key, value) in counts {
            if let Some(value) = value {
                dict.set_item(key, value)?;
            }
        }
        if let Some(basis) = usage.input_basis {
            dict.set_item("input_basis", basis)?;
        }
        if let Some(basis) = usage.output_basis {
            dict.set_item("output_basis", basis)?;
        }
        if let Some(parts) = &usage.input_tokens_by_modality {
            dict.set_item("input_tokens_by_modality", parts.clone())?;
        }
        if let Some(parts) = &usage.output_tokens_by_modality {
            dict.set_item("output_tokens_by_modality", parts.clone())?;
        }
        Ok(dict)
    }

    fn response_dict<'py>(
        py: Python<'py>,
        response: &libdd_ai_usage::ProviderResponse,
    ) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        match &response.usage {
            Some(usage) => dict.set_item("usage", usage_dict(py, usage)?)?,
            None => dict.set_item("usage", py.None())?,
        }
        dict.set_item("response_model", response.response_model.clone())?;
        dict.set_item("service_tier", response.service_tier.clone())?;
        Ok(dict)
    }

    /// What one provider response or stream event (a dict, or a list of
    /// events) reported: `{"usage": dict | None, "response_model", "service_tier"}`.
    /// `usage_format` is a `UsageFormat` name (`auto`, `openai_chat`, ...).
    #[pyfunction]
    #[pyo3(signature = (usage_format, payload, embeddings=false))]
    fn parse_provider_response<'py>(
        py: Python<'py>,
        usage_format: &str,
        payload: &Bound<'py, PyAny>,
        embeddings: bool,
    ) -> PyResult<Bound<'py, PyDict>> {
        let format = format_named(usage_format)?;
        let payload = to_json(payload, 0)?;
        let response = libdd_ai_usage::parse_provider_response(format, &payload, embeddings);
        response_dict(py, &response)
    }

    /// What a whole response body or stream (str or bytes, JSON or
    /// server-sent events) reported: `(response, outcome, error_name)`, with
    /// `outcome` one of `open`, `completed`, `failed`.
    #[pyfunction]
    #[pyo3(signature = (usage_format, body, embeddings=false))]
    fn parse_provider_body<'py>(
        py: Python<'py>,
        usage_format: &str,
        body: &Bound<'py, PyAny>,
        embeddings: bool,
    ) -> PyResult<(Bound<'py, PyDict>, &'static str, Option<String>)> {
        let format = format_named(usage_format)?;
        let (response, outcome, error) = if let Ok(bytes) = body.cast::<PyBytes>() {
            let bytes = bytes.as_bytes();
            py.detach(|| libdd_ai_usage::parse_provider_body(format, bytes, embeddings))
        } else if let Ok(text) = body.cast::<PyString>() {
            let text = text.to_str()?;
            py.detach(|| libdd_ai_usage::parse_provider_body(format, text.as_bytes(), embeddings))
        } else {
            return Err(PyTypeError::new_err("body must be str or bytes"));
        };
        Ok((response_dict(py, &response)?, outcome.as_str(), error))
    }

    /// A provider's stream read as it arrives, by the crate's shared rules:
    /// whole bytes with `feed`, server-sent events the caller already split
    /// with `feed_event`, or decoded events with `event`. `report` says what
    /// it reported so far; `take` reads a last unterminated line and ends it.
    #[pyclass]
    struct UsageStream {
        inner: Option<libdd_ai_usage::UsageStream>,
    }

    impl UsageStream {
        fn live(&mut self) -> PyResult<&mut libdd_ai_usage::UsageStream> {
            self.inner
                .as_mut()
                .ok_or_else(|| PyRuntimeError::new_err("the stream was taken"))
        }
    }

    #[pymethods]
    impl UsageStream {
        #[new]
        #[pyo3(signature = (usage_format, embeddings=false))]
        fn new(usage_format: &str, embeddings: bool) -> PyResult<Self> {
            Ok(Self {
                inner: Some(libdd_ai_usage::UsageStream::new(
                    format_named(usage_format)?,
                    embeddings,
                )),
            })
        }

        /// The next bytes of the stream (str or bytes); a chunk can end anywhere.
        fn feed(&mut self, chunk: &Bound<'_, PyAny>) -> PyResult<()> {
            let stream = self.live()?;
            if let Ok(bytes) = chunk.cast::<PyBytes>() {
                stream.feed(bytes.as_bytes());
            } else if let Ok(text) = chunk.cast::<PyString>() {
                stream.feed(text.to_str()?.as_bytes());
            } else {
                return Err(PyTypeError::new_err("a stream chunk must be str or bytes"));
            }
            Ok(())
        }

        /// One server-sent event already split from the stream: its `event`
        /// field (empty when it had none) and its `data`.
        #[pyo3(signature = (event_name, data))]
        fn feed_event(&mut self, event_name: &str, data: &str) -> PyResult<()> {
            self.live()?.feed_event(event_name, data);
            Ok(())
        }

        /// One event an SDK already decoded (a dict of JSON values).
        fn event(&mut self, event: &Bound<'_, PyAny>) -> PyResult<()> {
            let event = to_json(event, 0)?;
            self.live()?.event(&event);
            Ok(())
        }

        /// What the stream reported so far: `(response, outcome, error_name)`,
        /// with `outcome` one of `open`, `completed`, `failed`.
        fn report<'py>(
            &self,
            py: Python<'py>,
        ) -> PyResult<(Bound<'py, PyDict>, &'static str, Option<String>)> {
            let stream = self
                .inner
                .as_ref()
                .ok_or_else(|| PyRuntimeError::new_err("the stream was taken"))?;
            let (response, outcome, error) = stream.report();
            Ok((response_dict(py, &response)?, outcome.as_str(), error))
        }

        /// `report`, reading a last line that had no line ending, and end the
        /// stream: it cannot be fed afterwards.
        fn take<'py>(
            &mut self,
            py: Python<'py>,
        ) -> PyResult<(Bound<'py, PyDict>, &'static str, Option<String>)> {
            let stream = self
                .inner
                .take()
                .ok_or_else(|| PyRuntimeError::new_err("the stream was taken"))?;
            let (response, outcome, error) = stream.finish();
            Ok((response_dict(py, &response)?, outcome.as_str(), error))
        }
    }

    /// The `gen_ai.provider.name` of a provider as a gateway or SDK names it.
    #[pyfunction]
    fn gen_ai_provider_name(name: &str) -> Option<String> {
        libdd_ai_usage::gen_ai_provider_name(name)
    }

    /// The `error.type` of an error, from its name (class name, provider error
    /// type or code) and HTTP status. `failed` says the operation failed even
    /// when neither names a cause: it is then `api_error`.
    #[pyfunction]
    #[pyo3(signature = (name=None, status=None, failed=false))]
    fn classify_error(
        name: Option<&str>,
        status: Option<i64>,
        failed: bool,
    ) -> Option<&'static str> {
        let status = status.and_then(|status| u16::try_from(status).ok());
        libdd_ai_usage::classify_error(name, status)
            .or(failed.then_some(libdd_ai_usage::ERROR_TYPE_API))
    }

    #[pyfunction]
    fn is_service_tier(value: &str) -> bool {
        libdd_ai_usage::is_service_tier(value)
    }

    /// The lower-case names of the response headers that can name the account
    /// or project `provider_name` billed, in the order they are read.
    #[pyfunction]
    fn provider_id_headers(provider_name: &str) -> Vec<&'static str> {
        let mut names: Vec<&'static str> = Vec::new();
        for (provider, _, name) in libdd_ai_usage::PROVIDER_ID_HEADERS {
            if (provider_name.is_empty() || provider == provider_name) && !names.contains(&name) {
                names.push(name);
            }
        }
        names
    }

    /// The deployment attributes a provider's response names: the account
    /// and project from its `headers` (lower-case names), and for
    /// `aws.bedrock` the account the first model ARN among `models` names.
    #[pyfunction]
    #[pyo3(signature = (provider_name, headers, models=Vec::new()))]
    fn provider_ids(
        provider_name: &str,
        headers: BTreeMap<String, String>,
        models: Vec<String>,
    ) -> BTreeMap<String, String> {
        let mut attributes: Attributes =
            libdd_ai_usage::provider_id_attributes(provider_name, |name| {
                headers.get(name).map(String::as_str)
            });
        if provider_name.is_empty() || provider_name == "aws.bedrock" {
            if let Some(account) = models
                .iter()
                .find_map(|model| libdd_ai_usage::bedrock_arn_account(model))
            {
                attributes
                    .entry("trajectory.request.provider_account_id".to_owned())
                    .or_insert_with(|| account.to_owned());
            }
        }
        attributes.into_iter().collect()
    }

    /// The metric names of `profile_id`, or of every profile when it is empty.
    #[pyfunction]
    #[pyo3(signature = (profile_id=""))]
    fn known_metrics(profile_id: &str) -> PyResult<Vec<&'static str>> {
        let profiles = if profile_id.is_empty() {
            Profile::ALL.to_vec()
        } else {
            vec![profile(profile_id)?]
        };
        Ok(profiles
            .iter()
            .flat_map(|profile| profile.metric_names().iter().copied())
            .collect())
    }

    /// DogStatsD lines packed into datagrams of at most `max_payload` bytes,
    /// and the number of lines dropped for being longer than that.
    #[pyfunction]
    #[pyo3(signature = (lines, max_payload, constant_tags=Vec::new()))]
    fn pack_dogstatsd(
        lines: Vec<String>,
        max_payload: usize,
        constant_tags: Vec<String>,
    ) -> (Vec<String>, usize) {
        libdd_ai_usage::pack_dogstatsd_counted(&lines, &constant_tags, max_payload)
    }

    /// A rejected observation: `ValueError(error_code, message)`.
    fn rejected(error: MetricError) -> PyErr {
        PyValueError::new_err((error.code().as_str(), error.message().to_owned()))
    }

    fn profile(id: &str) -> PyResult<Profile> {
        Profile::from_id(id)
            .ok_or_else(|| PyValueError::new_err(("profile_invalid", id.to_owned())))
    }

    /// One series of a batch: its resource and profile, and its points (a
    /// Counter's sum, or every sample of a Histogram).
    struct Series {
        resource: Attributes,
        profile: Profile,
        points: Vec<MetricPoint>,
    }

    /// The series of a batch, in its order. `MetricBatch::points` lists the
    /// samples of one Histogram series one after another.
    fn series_of(batch: &MetricBatch) -> Vec<Series> {
        let mut series: Vec<Series> = Vec::new();
        for (resource, profile, points) in batch.points() {
            let start = series.len();
            for point in points {
                let same = series.len() > start
                    && series.last().is_some_and(|last| {
                        let first = &last.points[0];
                        first.name == point.name
                            && first.instrument == point.instrument
                            && first.attributes == point.attributes
                    });
                match series.last_mut() {
                    Some(last) if same => last.points.push(point),
                    _ => series.push(Series {
                        resource: resource.clone(),
                        profile,
                        points: vec![point],
                    }),
                }
            }
        }
        series
    }

    impl Series {
        /// A rough size of the series in an OTLP request: its attributes, and
        /// a data point (a Histogram's buckets are encoded once per series).
        fn estimate(&self) -> usize {
            let point = &self.points[0];
            let attributes: usize = point
                .attributes
                .iter()
                .map(|(key, value)| key.len() + value.len() + 8)
                .sum();
            attributes
                + match point.instrument {
                    Instrument::Counter => 32,
                    Instrument::Histogram => 160,
                }
        }
    }

    /// A batch of the given series. Counter sums are added back on their
    /// exact integer, Histogram samples in their order.
    fn batch_of(series: Vec<Series>) -> Result<MetricBatch, MetricError> {
        let mut batch = MetricBatch::new();
        for one in series {
            batch.add(
                one.profile,
                &Projection {
                    points: one.points,
                    issues: Issues::new(),
                    resource: one.resource,
                },
            )?;
        }
        Ok(batch)
    }

    /// Encode `series` into `parts`, each at most `max_bytes` unless it holds
    /// a single series: a request that does not fit is cut into as many even
    /// pieces as its size needs. One series is never split, so no two points
    /// of a series in one window are sent apart.
    fn encode_series(
        series: Vec<Series>,
        encode: &dyn Fn(&MetricBatch) -> Vec<u8>,
        max_bytes: usize,
        parts: &mut Vec<Vec<u8>>,
    ) -> Result<(), MetricError> {
        let count = series.len();
        let batch = batch_of(series)?;
        let payload = encode(&batch);
        if payload.len() <= max_bytes || count <= 1 {
            parts.push(payload);
            return Ok(());
        }
        let series = series_of(&batch);
        drop(batch);
        let pieces = (payload.len() / target(max_bytes) + 1).clamp(2, count);
        let size = count.div_ceil(pieces);
        let mut series = series.into_iter();
        loop {
            let piece: Vec<Series> = series.by_ref().take(size).collect();
            if piece.is_empty() {
                return Ok(());
            }
            encode_series(piece, encode, max_bytes, parts)?;
        }
    }

    /// The size aimed at when packing a request, with room for the estimate's
    /// error.
    fn target(max_bytes: usize) -> usize {
        (max_bytes - max_bytes / 8).max(1)
    }

    /// The batch as OTLP requests of at most `max_bytes` each. A batch that
    /// fits is encoded as one request, exactly as `take_otlp` encodes it.
    fn encode_parts(
        batch: &MetricBatch,
        encode: &dyn Fn(&MetricBatch) -> Vec<u8>,
        max_bytes: usize,
    ) -> Result<Vec<Vec<u8>>, MetricError> {
        let whole = encode(batch);
        if whole.len() <= max_bytes {
            return Ok(vec![whole]);
        }
        let series = series_of(batch);
        // Pack series in order by their estimated size, scaled so that the
        // estimates add up to the whole request's size: most requests fit on
        // the first try, and the batch is encoded about twice in all.
        let estimates: Vec<usize> = series.iter().map(Series::estimate).collect();
        let scale = whole.len() as f64 / estimates.iter().sum::<usize>().max(1) as f64;
        let target = target(max_bytes) as f64;
        let mut parts = Vec::new();
        let mut chunk = Vec::new();
        let mut size = 0.0;
        for (one, estimate) in series.into_iter().zip(estimates) {
            let estimate = estimate as f64 * scale;
            if !chunk.is_empty() && size + estimate > target {
                encode_series(std::mem::take(&mut chunk), encode, max_bytes, &mut parts)?;
                size = 0.0;
            }
            size += estimate;
            chunk.push(one);
        }
        if !chunk.is_empty() {
            encode_series(chunk, encode, max_bytes, &mut parts)?;
        }
        Ok(parts)
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
        /// The deployment attributes of every observation, such as the
        /// service, env and version; an observation's own win.
        deployment_attributes: BTreeMap<String, String>,
    }

    impl UsageMetrics {
        /// Project one observation under `profile` with the batch's and the
        /// observation's deployment attributes, keeping the batch's metrics.
        fn project(
            &self,
            profile: Profile,
            observation: &Json,
            deployment_attributes: Option<BTreeMap<String, String>>,
        ) -> Result<(Projection, Vec<String>), MetricError> {
            let mut projection = project_result(profile, observation)?;
            let attributes = match deployment_attributes {
                Some(own) if !self.deployment_attributes.is_empty() => {
                    let mut merged = self.deployment_attributes.clone();
                    merged.extend(own);
                    merged
                }
                Some(own) => own,
                None => self.deployment_attributes.clone(),
            };
            if !attributes.is_empty() {
                projection = with_deployment_attributes(projection, &attributes)?;
            }
            let issues = projection
                .issues
                .iter()
                .map(|issue| issue.as_str().to_owned())
                .collect();
            if let Some(metrics) = &self.metrics {
                projection
                    .points
                    .retain(|point| metrics.contains(&point.name));
            }
            Ok((projection, issues))
        }

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
        /// a profile defines is kept. A name no profile defines raises
        /// `ValueError("metric_unknown", name)`. `deployment_attributes` go on
        /// every observation recorded, under the observation's own.
        #[new]
        #[pyo3(signature = (metrics=None, deployment_attributes=None))]
        fn new(
            metrics: Option<Vec<String>>,
            deployment_attributes: Option<BTreeMap<String, String>>,
        ) -> PyResult<Self> {
            if let Some(names) = &metrics {
                let known: BTreeSet<&str> = Profile::ALL
                    .iter()
                    .flat_map(|profile| profile.metric_names().iter().copied())
                    .collect();
                if let Some(unknown) = names.iter().find(|name| !known.contains(name.as_str())) {
                    return Err(PyValueError::new_err(("metric_unknown", unknown.clone())));
                }
            }
            Ok(Self {
                batch: Mutex::new(MetricBatch::new()),
                metrics: metrics.map(|names| names.into_iter().collect()),
                deployment_attributes: deployment_attributes.unwrap_or_default(),
            })
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
                let (projection, issues) = self
                    .project(profile, &observation, deployment_attributes)
                    .map_err(rejected)?;
                self.batch
                    .lock()
                    .map_err(|_| PyRuntimeError::new_err("usage metrics lock poisoned"))?
                    .try_add_owned(profile, projection)
                    .map_err(rejected)?;
                Ok(issues)
            })
        }

        /// `record` under each of `profile_ids` (as one provider attempt and
        /// its token breakdown): every projection is added, or none. Returns
        /// the issue codes of each profile, in order. Raises
        /// `ValueError(error_code, message)` naming the profile that rejected
        /// the observation; nothing is recorded then.
        #[pyo3(signature = (profile_ids, observation, deployment_attributes=None))]
        fn record_all(
            &self,
            py: Python<'_>,
            profile_ids: Vec<String>,
            observation: &Bound<'_, PyDict>,
            deployment_attributes: Option<BTreeMap<String, String>>,
        ) -> PyResult<Vec<Vec<String>>> {
            let profiles = profile_ids
                .iter()
                .map(|id| profile(id))
                .collect::<PyResult<Vec<Profile>>>()?;
            let observation = to_json(observation.as_any(), 0)?;
            py.detach(|| {
                let mut projections = Vec::with_capacity(profiles.len());
                let mut issues = Vec::with_capacity(profiles.len());
                for profile in &profiles {
                    let (projection, found) = self
                        .project(*profile, &observation, deployment_attributes.clone())
                        .map_err(|error| {
                            PyValueError::new_err((
                                error.code().as_str(),
                                error.message().to_owned(),
                                profile.id(),
                            ))
                        })?;
                    projections.push((*profile, projection));
                    issues.push(found);
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

        /// Take the points recorded so far as an OTLP
        /// `ExportMetricsServiceRequest` in protobuf, or `None` when there are
        /// none. Delta points cover `start_time_unix_nano` to `time_unix_nano`.
        /// `datadog` selects the Datadog binding, for an export Datadog stores as
        /// it arrives: cost in USD and versioned values with `/`, as over
        /// DogStatsD.
        #[pyo3(signature = (scope_name, scope_version, start_time_unix_nano, time_unix_nano, datadog=false))]
        fn take_otlp<'py>(
            &self,
            py: Python<'py>,
            scope_name: &str,
            scope_version: &str,
            start_time_unix_nano: u64,
            time_unix_nano: u64,
            datadog: bool,
        ) -> PyResult<Option<Bound<'py, PyBytes>>> {
            let binding = if datadog {
                OtlpBinding::Datadog
            } else {
                OtlpBinding::OpenTelemetry
            };
            let batch = self.take()?;
            if batch.is_empty() {
                return Ok(None);
            }
            let payload = py.detach(|| {
                batch.encode_otlp_with(
                    OtlpScope {
                        name: scope_name,
                        version: scope_version,
                    },
                    OtlpWindow {
                        start_time_unix_nano,
                        time_unix_nano,
                    },
                    binding,
                )
            });
            Ok(Some(PyBytes::new(py, &payload)))
        }

        /// Take the points recorded so far as OTLP `ExportMetricsServiceRequest`s
        /// of at most `max_bytes` each, as `take_otlp` encodes them; an empty
        /// list when there are none. The points are split by series, and the
        /// points of one series always go in the same request, which can then
        /// exceed `max_bytes`. A batch that fits gives one request, the same
        /// bytes `take_otlp` returns.
        #[pyo3(signature = (scope_name, scope_version, start_time_unix_nano, time_unix_nano, max_bytes, datadog=false))]
        #[allow(clippy::too_many_arguments)]
        fn take_otlp_parts<'py>(
            &self,
            py: Python<'py>,
            scope_name: &str,
            scope_version: &str,
            start_time_unix_nano: u64,
            time_unix_nano: u64,
            max_bytes: usize,
            datadog: bool,
        ) -> PyResult<Vec<Bound<'py, PyBytes>>> {
            let binding = if datadog {
                OtlpBinding::Datadog
            } else {
                OtlpBinding::OpenTelemetry
            };
            let batch = self.take()?;
            if batch.is_empty() {
                return Ok(Vec::new());
            }
            let scope = OtlpScope {
                name: scope_name,
                version: scope_version,
            };
            let window = OtlpWindow {
                start_time_unix_nano,
                time_unix_nano,
            };
            let encode = |batch: &MetricBatch| batch.encode_otlp_with(scope, window, binding);
            let parts = py
                .detach(|| encode_parts(&batch, &encode, max_bytes))
                .map_err(rejected)?;
            Ok(parts.iter().map(|part| PyBytes::new(py, part)).collect())
        }

        /// Take the points recorded so far as DogStatsD lines, one per series
        /// point, without newlines.
        fn take_dogstatsd(&self, py: Python<'_>) -> PyResult<Vec<String>> {
            let batch = self.take()?;
            py.detach(|| batch.dogstatsd_lines()).map_err(rejected)
        }

        /// Take the points recorded so far as DogStatsD datagrams of at most
        /// `max_payload` bytes, NUL-separated in one bytes object, and the
        /// number of lines dropped for being longer than `max_payload`.
        fn take_dogstatsd_datagrams<'py>(
            &self,
            py: Python<'py>,
            max_payload: usize,
        ) -> PyResult<(Bound<'py, PyBytes>, usize)> {
            let batch = self.take()?;
            let (text, dropped) = py
                .detach(|| {
                    let lines = batch.dogstatsd_lines()?;
                    Ok(libdd_ai_usage::pack_dogstatsd_joined(
                        lines.iter().map(String::as_str),
                        &[] as &[&str],
                        max_payload,
                        '\0',
                    ))
                })
                .map_err(rejected)?;
            Ok((PyBytes::new(py, text.as_bytes()), dropped))
        }
    }
}
