use data_pipeline::trace_exporter::{
    agent_response::AgentResponse, TelemetryConfig, TraceExporter, TraceExporterBuilder,
    TraceExporterInputFormat, TraceExporterOutputFormat,
};
use pyo3::{exceptions::PyValueError, prelude::*, pybacked::PyBackedBytes};
use std::time::Duration;
mod exceptions;
use exceptions::TraceExporterErrorPy;

/// A wrapper around [TraceExporterBuilder]
///
/// Allows to use the builder as a python class. Only one exporter can be built using a builder
/// once `build` has been called the builder shouldn't be reused.
#[pyclass(name = "TraceExporterBuilder")]
pub struct TraceExporterBuilderPy {
    builder: Option<TraceExporterBuilder>,
}

impl TraceExporterBuilderPy {
    fn try_as_mut(&mut self) -> PyResult<&mut TraceExporterBuilder> {
        self.builder
            .as_mut()
            .ok_or(PyValueError::new_err("Builder has already been consumed"))
    }
}

#[pymethods]
impl TraceExporterBuilderPy {
    #[new]
    fn new() -> Self {
        TraceExporterBuilderPy {
            builder: Some(TraceExporterBuilder::default()),
        }
    }

    fn set_hostname(mut slf: PyRefMut<'_, Self>, hostname: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_hostname(hostname);
        Ok(slf.into())
    }

    fn set_url(mut slf: PyRefMut<'_, Self>, url: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_url(url);
        Ok(slf.into())
    }

    fn set_dogstatsd_url(mut slf: PyRefMut<'_, Self>, url: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_dogstatsd_url(url);
        Ok(slf.into())
    }

    fn set_env(mut slf: PyRefMut<'_, Self>, env: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_env(env);
        Ok(slf.into())
    }

    fn set_app_version(mut slf: PyRefMut<'_, Self>, version: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_app_version(version);
        Ok(slf.into())
    }

    fn set_service(mut slf: PyRefMut<'_, Self>, service: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_service(service);
        Ok(slf.into())
    }

    fn set_git_commit_sha(
        mut slf: PyRefMut<'_, Self>,
        git_commit_sha: &'_ str,
    ) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_git_commit_sha(git_commit_sha);
        Ok(slf.into())
    }

    fn set_tracer_version(mut slf: PyRefMut<'_, Self>, version: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_tracer_version(version);
        Ok(slf.into())
    }

    fn set_language(mut slf: PyRefMut<'_, Self>, language: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_language(language);
        Ok(slf.into())
    }

    fn set_language_version(mut slf: PyRefMut<'_, Self>, version: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_language_version(version);
        Ok(slf.into())
    }

    fn set_language_interpreter(
        mut slf: PyRefMut<'_, Self>,
        interpreter: &'_ str,
    ) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_language_interpreter(interpreter);
        Ok(slf.into())
    }

    fn set_language_interpreter_vendor(
        mut slf: PyRefMut<'_, Self>,
        vendor: &'_ str,
    ) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_language_interpreter_vendor(vendor);
        Ok(slf.into())
    }

    fn set_test_session_token(mut slf: PyRefMut<'_, Self>, token: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_test_session_token(token);
        Ok(slf.into())
    }

    fn set_input_format(mut slf: PyRefMut<'_, Self>, input_format: &str) -> PyResult<Py<Self>> {
        let input_format = match input_format {
            "v0.4" => Ok(TraceExporterInputFormat::V04),
            "v0.5" => Ok(TraceExporterInputFormat::V05),
            _ => Err(PyValueError::new_err("Invalid trace format")),
        }?;
        slf.try_as_mut()?.set_input_format(input_format);
        Ok(slf.into())
    }

    fn set_output_format(mut slf: PyRefMut<'_, Self>, output_format: &str) -> PyResult<Py<Self>> {
        let output_format = match output_format {
            "v0.4" => Ok(TraceExporterOutputFormat::V04),
            "v0.5" => Ok(TraceExporterOutputFormat::V05),
            _ => Err(PyValueError::new_err("Invalid trace format")),
        }?;
        slf.try_as_mut()?.set_output_format(output_format);
        Ok(slf.into())
    }

    fn set_client_computed_top_level(mut slf: PyRefMut<'_, Self>) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_client_computed_top_level();
        Ok(slf.into())
    }

    fn set_client_computed_stats(mut slf: PyRefMut<'_, Self>) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_client_computed_stats();
        Ok(slf.into())
    }

    fn enable_stats(mut slf: PyRefMut<'_, Self>, bucket_size_ns: u64) -> PyResult<Py<Self>> {
        slf.try_as_mut()?
            .enable_stats(Duration::from_nanos(bucket_size_ns));
        Ok(slf.into())
    }

    fn enable_telemetry(
        mut slf: PyRefMut<'_, Self>,
        heartbeat_ms: u64,
        runtime_id: String,
    ) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.enable_telemetry(Some(TelemetryConfig {
            heartbeat: heartbeat_ms,
            runtime_id: Some(runtime_id),
            debug_enabled: true,
        }));
        Ok(slf.into())
    }

    /// Consumes the wrapped builder.
    ///
    /// The builder shouldn't be reused
    fn build(&mut self) -> PyResult<TraceExporterPy> {
        let exporter = TraceExporterPy {
            inner: Some(
                self.builder
                    .take()
                    .ok_or(PyValueError::new_err("Builder has already been consumed"))?
                    .build()
                    .map_err(|err| PyValueError::new_err(format!("Builder {err}")))?,
            ),
        };
        Ok(exporter)
    }

    fn debug(&self) -> String {
        format!("{:?}", self.builder)
    }
}

/// A python object wrapping a [TraceExporter] instance
#[pyclass(name = "TraceExporter")]
pub struct TraceExporterPy {
    inner: Option<TraceExporter>,
}

#[pymethods]
impl TraceExporterPy {
    /// Send a msgpack encoded trace payload.
    ///
    /// The payload is passed as an immutable `bytes` object to be able to release the GIL while
    /// sending the traces.
    fn send(&self, py: Python<'_>, data: PyBackedBytes, trace_count: usize) -> PyResult<String> {
        py.allow_threads(move || {
            match self
                .inner
                .as_ref()
                .ok_or(PyValueError::new_err(
                    "TraceExporter has already been consumed",
                ))?
                .send(&data, trace_count)
            {
                Ok(res) => match res {
                    AgentResponse::Changed { body } => Ok(body),
                    AgentResponse::Unchanged => Ok("".to_string()),
                },
                Err(e) => Err(TraceExporterErrorPy::from(e).into()),
            }
        })
    }

    fn shutdown(&mut self, timeout_ns: u64) -> PyResult<()> {
        match self
            .inner
            .take()
            .ok_or(PyValueError::new_err(
                "TraceExporter has already been consumed",
            ))?
            .shutdown(Some(Duration::from_nanos(timeout_ns)))
        {
            Ok(_) => Ok(()),
            Err(e) => Err(TraceExporterErrorPy::from(e).into()),
        }
    }

    fn drop(&mut self) -> PyResult<()> {
        drop(self.inner.take());
        Ok(())
    }

    fn run_worker(&self) -> PyResult<()> {
        let exporter = self.inner.as_ref().ok_or(PyValueError::new_err(
            "TraceExporter has already been consumed",
        ))?;
        match exporter.run_worker() {
            Ok(_) => Ok(()),
            Err(e) => Err(TraceExporterErrorPy::from(e).into()),
        }
    }

    fn stop_worker(&self) -> PyResult<()> {
        if let Some(exporter) = self.inner.as_ref() {
            exporter.stop_worker();
        }
        Ok(())
    }

    fn debug(&self) -> String {
        format!("{:?}", self.inner)
    }
}

#[pymodule]
pub fn register_data_pipeline(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TraceExporterBuilderPy>()?;
    m.add_class::<TraceExporterPy>()?;
    exceptions::register_exceptions(m)?;

    Ok(())
}
