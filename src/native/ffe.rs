// FFE (Feature Flagging and Experimentation) module.

use pyo3::pymodule;

#[pymodule]
pub mod ffe {
    use std::collections::HashMap;

    use pyo3::{exceptions::PyValueError, prelude::*};
    use tracing::debug;

    use datadog_ffe::rules_based as ffe;
    use datadog_ffe::rules_based::{
        get_assignment, now, AssignmentReason, AssignmentValue, Configuration, EvaluationContext,
        EvaluationError, Str, UniversalFlagConfig,
    };
    use datadog_ffe_ffi::{
        FfeSourceApplyError, FfeSourcePollOutcome, FfeSourceState, FfeSourceStateConfig,
    };

    #[pyclass(frozen)]
    #[pyo3(name = "Configuration")]
    struct FfeConfiguration {
        inner: Configuration,
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    #[pyclass(eq, eq_int, from_py_object)]
    enum FlagType {
        String,
        Integer,
        Float,
        Boolean,
        Object,
    }

    #[pyclass(frozen)]
    struct ResolutionDetails {
        #[pyo3(get)]
        value: Option<AssignmentValue>,
        #[pyo3(get)]
        error_code: Option<ErrorCode>,
        #[pyo3(get)]
        error_message: Option<Str>,
        #[pyo3(get)]
        reason: Option<Reason>,
        #[pyo3(get)]
        variant: Option<Str>,
        #[pyo3(get)]
        allocation_key: Option<Str>,
        #[pyo3(get)]
        flag_metadata: HashMap<Str, Str>,
        #[pyo3(get)]
        do_log: bool,
    }

    #[pyclass]
    #[pyo3(name = "HybridSourceState")]
    struct HybridSourceState {
        inner: FfeSourceState,
    }

    #[pyclass(frozen)]
    struct HybridSourcePollOutcome {
        #[pyo3(get)]
        status: Str,
        #[pyo3(get)]
        status_code: Option<u16>,
        #[pyo3(get)]
        applied: bool,
        #[pyo3(get)]
        unchanged: bool,
        #[pyo3(get)]
        skipped: bool,
        #[pyo3(get)]
        attempts: u32,
        #[pyo3(get)]
        etag: Option<Str>,
        #[pyo3(get)]
        error: Option<Str>,
        #[pyo3(get)]
        retryable: bool,
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    #[pyclass(eq, eq_int, skip_from_py_object)]
    enum Reason {
        Static,
        Default,
        TargetingMatch,
        Split,
        Cached,
        Disabled,
        Unknown,
        Stale,
        Error,
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    #[pyclass(eq, eq_int, skip_from_py_object)]
    enum ErrorCode {
        /// The type of the flag value does not match the expected type.
        TypeMismatch,
        /// An error occured during parsing configuration.
        ParseError,
        /// Flog is disabled or not found.
        FlagNotFound,
        TargetingKeyMissing,
        InvalidContext,
        ProviderNotReady,
        /// Catch-all / unknown error.
        General,
    }

    #[pymethods]
    impl FfeConfiguration {
        /// Process feature flag configuration rules.
        ///
        /// This function receives raw bytes containing the configuration data from Remote Configuration
        /// and creates `FfeConfiguration` that can be used to evaluate feature flags..
        ///
        /// # Arguments
        /// * `config_bytes` - Raw bytes containing the configuration data
        #[new]
        fn new(config_bytes: Vec<u8>) -> PyResult<FfeConfiguration> {
            debug!(
                "Processing FFE configuration, size: {} bytes",
                config_bytes.len()
            );

            let configuration = Configuration::from_server_response(
                UniversalFlagConfig::from_json(config_bytes).map_err(|err| {
                    debug!("Failed to parse FFE configuration: {err}");
                    PyValueError::new_err(format!("failed to parse configuration: {err}"))
                })?,
            );

            Ok(FfeConfiguration {
                inner: configuration,
            })
        }

        fn resolve_value<'py>(
            &self,
            flag_key: &str,
            expected_type: FlagType,
            context: Bound<'py, PyAny>,
        ) -> PyResult<ResolutionDetails> {
            let context = match context.extract::<EvaluationContext>() {
                Ok(context) => context,
                Err(err) => {
                    return Ok(ResolutionDetails::error(
                        ErrorCode::InvalidContext,
                        err.to_string(),
                    ))
                }
            };

            let assignment = get_assignment(
                Some(&self.inner),
                flag_key,
                &context,
                expected_type.into(),
                now(),
            );

            let result = match assignment {
                Ok(assignment) => ResolutionDetails {
                    value: Some(assignment.value),
                    error_code: None,
                    error_message: None,
                    reason: Some(assignment.reason.into()),
                    variant: Some(assignment.variation_key),
                    allocation_key: Some(assignment.allocation_key.clone()),
                    flag_metadata: [("allocation_key".into(), assignment.allocation_key)]
                        .into_iter()
                        .collect(),
                    do_log: assignment.do_log,
                },
                Err(err) => err.into(),
            };

            Ok(result)
        }
    }

    #[pymethods]
    impl HybridSourceState {
        #[new]
        fn new(
            base_url: String,
            api_key: Option<String>,
            request_timeout_seconds: f64,
            max_retries: u32,
            backoff_base_seconds: f64,
        ) -> PyResult<HybridSourceState> {
            let config = FfeSourceStateConfig {
                base_url,
                api_key,
                request_timeout: duration_from_positive_seconds(
                    request_timeout_seconds,
                    "request_timeout_seconds",
                )?,
                max_retries,
                backoff_base: duration_from_non_negative_seconds(
                    backoff_base_seconds,
                    "backoff_base_seconds",
                )?,
            };
            let inner = FfeSourceState::new(config).map_err(to_py_source_state_value_error)?;
            Ok(HybridSourceState { inner })
        }

        fn poll_once(&self) -> HybridSourcePollOutcome {
            match self.inner.poll_once() {
                Ok(outcome) => HybridSourcePollOutcome::from_outcome(outcome),
                Err(err) => HybridSourcePollOutcome::from_error(err),
            }
        }

        #[getter]
        fn is_ready(&self) -> bool {
            self.inner.is_ready()
        }

        #[getter]
        fn last_etag(&self) -> Option<String> {
            self.inner.last_etag()
        }

        fn resolve_value<'py>(
            &self,
            flag_key: &str,
            expected_type: FlagType,
            context: Bound<'py, PyAny>,
        ) -> PyResult<ResolutionDetails> {
            let context = match context.extract::<EvaluationContext>() {
                Ok(context) => context,
                Err(err) => {
                    return Ok(ResolutionDetails::error(
                        ErrorCode::InvalidContext,
                        err.to_string(),
                    ))
                }
            };

            match self
                .inner
                .resolve_value(flag_key, expected_type.into(), &context)
            {
                Ok(result) => Ok(match result {
                    Ok(assignment) => assignment.into(),
                    Err(err) => err.into(),
                }),
                Err(err) => Ok(ResolutionDetails::error(ErrorCode::General, err.to_string())),
            }
        }
    }

    impl ResolutionDetails {
        fn empty(reason: impl Into<Reason>) -> ResolutionDetails {
            ResolutionDetails {
                value: None,
                error_code: None,
                error_message: None,
                reason: Some(reason.into()),
                variant: None,
                allocation_key: None,
                flag_metadata: HashMap::new(),
                do_log: false,
            }
        }

        fn error(code: impl Into<ErrorCode>, message: impl Into<Str>) -> ResolutionDetails {
            ResolutionDetails {
                value: None,
                error_code: Some(code.into()),
                error_message: Some(message.into()),
                reason: Some(Reason::Error),
                variant: None,
                allocation_key: None,
                flag_metadata: HashMap::new(),
                do_log: false,
            }
        }
    }

    impl HybridSourcePollOutcome {
        fn from_outcome(outcome: FfeSourcePollOutcome) -> Self {
            HybridSourcePollOutcome {
                status: outcome.name().into(),
                status_code: Some(outcome.status_code()),
                applied: outcome.applied(),
                unchanged: outcome.unchanged(),
                skipped: false,
                attempts: outcome.attempts(),
                etag: outcome.etag().map(Into::into),
                error: None,
                retryable: false,
            }
        }

        fn from_error(error: FfeSourceApplyError) -> Self {
            HybridSourcePollOutcome {
                status: "error".into(),
                status_code: error.status_code(),
                applied: false,
                unchanged: false,
                skipped: false,
                attempts: 0,
                etag: None,
                error: Some(error.to_string().into()),
                retryable: error.retryable(),
            }
        }

    }

    impl From<EvaluationError> for ResolutionDetails {
        fn from(value: EvaluationError) -> ResolutionDetails {
            match value {
                EvaluationError::TypeMismatch { expected, found } => ResolutionDetails::error(
                    ErrorCode::TypeMismatch,
                    format!("type mismatch, expected={expected:?}, found={found:?}"),
                ),
                EvaluationError::ConfigurationParseError => {
                    ResolutionDetails::error(ErrorCode::ParseError, "configuration error")
                }
                EvaluationError::ConfigurationMissing => ResolutionDetails::error(
                    ErrorCode::ProviderNotReady,
                    "configuration is missing",
                ),
                EvaluationError::FlagUnrecognizedOrDisabled => ResolutionDetails::error(
                    ErrorCode::FlagNotFound,
                    "flag is unrecognized or disabled",
                ),
                EvaluationError::FlagDisabled => ResolutionDetails::empty(Reason::Disabled),
                EvaluationError::DefaultAllocationNull => ResolutionDetails::empty(Reason::Default),
                // libdatadog returns TargetingKeyMissing when a flag has shard-based
                // allocation but no targeting key was provided (nothing to hash).
                // See: https://github.com/DataDog/libdatadog/blob/1b7b2daf790f/datadog-ffe/src/rules_based/eval/eval_assignment.rs#L186
                EvaluationError::TargetingKeyMissing => ResolutionDetails::error(
                    ErrorCode::TargetingKeyMissing,
                    "targeting key is missing",
                ),
                err => ResolutionDetails::error(ErrorCode::General, err.to_string()),
            }
        }
    }

    impl From<ffe::Assignment> for ResolutionDetails {
        fn from(value: ffe::Assignment) -> ResolutionDetails {
            ResolutionDetails {
                value: Some(value.value),
                error_code: None,
                error_message: None,
                reason: Some(value.reason.into()),
                variant: Some(value.variation_key),
                allocation_key: Some(value.allocation_key.clone()),
                flag_metadata: [("allocation_key".into(), value.allocation_key)]
                    .into_iter()
                    .collect(),
                do_log: value.do_log,
            }
        }
    }

    impl From<FlagType> for ffe::ExpectedFlagType {
        fn from(value: FlagType) -> ffe::ExpectedFlagType {
            match value {
                FlagType::String => ffe::ExpectedFlagType::String,
                FlagType::Integer => ffe::ExpectedFlagType::Integer,
                FlagType::Float => ffe::ExpectedFlagType::Float,
                FlagType::Boolean => ffe::ExpectedFlagType::Boolean,
                FlagType::Object => ffe::ExpectedFlagType::Object,
            }
        }
    }

    impl From<AssignmentReason> for Reason {
        fn from(value: AssignmentReason) -> Self {
            match value {
                AssignmentReason::TargetingMatch => Reason::TargetingMatch,
                AssignmentReason::Split => Reason::Split,
                AssignmentReason::Static => Reason::Static,
                AssignmentReason::Default => Reason::Default,
            }
        }
    }

    fn duration_from_positive_seconds(value: f64, name: &str) -> PyResult<std::time::Duration> {
        if !value.is_finite() || value <= 0.0 {
            return Err(PyValueError::new_err(format!("{name} must be positive")));
        }
        Ok(std::time::Duration::from_secs_f64(value))
    }

    fn duration_from_non_negative_seconds(value: f64, name: &str) -> PyResult<std::time::Duration> {
        if !value.is_finite() || value < 0.0 {
            return Err(PyValueError::new_err(format!("{name} must be non-negative")));
        }
        Ok(std::time::Duration::from_secs_f64(value))
    }

    fn to_py_source_state_value_error(error: FfeSourceApplyError) -> PyErr {
        PyValueError::new_err(error.to_string())
    }
}
