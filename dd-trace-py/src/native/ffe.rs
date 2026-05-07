// FFE (Feature Flagging and Experimentation) module.

use pyo3::pymodule;

#[pymodule]
pub mod ffe {
    use std::{collections::HashMap, sync::Arc};

    use pyo3::{exceptions::PyValueError, prelude::*};
    use tracing::debug;

    use datadog_ffe::rules_based as ffe;
    use datadog_ffe::rules_based::{
        get_assignment, now, AssignmentReason, AssignmentValue, Configuration, EvaluationContext,
        EvaluationError, Str, UniversalFlagConfig,
    };

    #[pyclass(frozen)]
    #[pyo3(name = "Configuration")]
    struct FfeConfiguration {
        inner: Configuration,
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    #[pyclass(eq, eq_int)]
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
        extra_logging: Option<Arc<HashMap<String, String>>>,
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    #[pyclass(eq, eq_int)]
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
    #[pyclass(eq, eq_int)]
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
                    extra_logging: Some(assignment.extra_logging),
                },
                Err(err) => err.into(),
            };

            Ok(result)
        }
    }

    #[pymethods]
    impl ResolutionDetails {
        // pyo3 refuses to implement IntoPyObject for Arc, so we need to do this dance with
        // returning a reference.
        #[getter]
        fn extra_logging(&self) -> Option<&HashMap<String, String>> {
            self.extra_logging.as_ref().map(|it| it.as_ref())
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
                extra_logging: None,
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
                extra_logging: None,
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
                err => ResolutionDetails::error(ErrorCode::General, err.to_string()),
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
            }
        }
    }
}
