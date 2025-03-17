use data_pipeline::trace_exporter::{
    TraceExporter, TraceExporterBuilder, TraceExporterInputFormat, TraceExporterOutputFormat,
};
use pyo3::{exceptions::PyValueError, prelude::*, pybacked::PyBackedBytes};
use tinybytes::Bytes;
mod exceptions;

#[pyclass(eq, eq_int)]
#[derive(PartialEq)]
pub enum TraceFormat {
    V04,
    V05,
}

impl TryFrom<&TraceFormat> for TraceExporterInputFormat {
    type Error = PyErr;
    fn try_from(value: &TraceFormat) -> Result<Self, Self::Error> {
        match value {
            TraceFormat::V04 => Ok(Self::V04),
            TraceFormat::V05 => Ok(Self::V05),
        }
    }
}

impl TryFrom<&TraceFormat> for TraceExporterOutputFormat {
    type Error = PyErr;
    fn try_from(value: &TraceFormat) -> Result<Self, Self::Error> {
        match value {
            TraceFormat::V04 => Ok(Self::V04),
            TraceFormat::V05 => Ok(Self::V05),
        }
    }
}

/// A wrapper arround [TraceExporterBuilder]
///
/// Allows to use the builder as a python class. Only one exporter can be built using a builder
/// once `build` has been called the builder shouldn't be used.
#[pyclass(
    name = "TraceExporterBuilder",
    module = "ddtrace.internal._native.data_pipeline"
)]
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

    fn set_input_format(mut slf: PyRefMut<'_, Self>, vendor: &TraceFormat) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_input_format(vendor.try_into()?);
        Ok(slf.into())
    }

    fn set_output_format(mut slf: PyRefMut<'_, Self>, vendor: &TraceFormat) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_output_format(vendor.try_into()?);
        Ok(slf.into())
    }

    /// Consumes the wrapped builder.
    ///
    /// The builder shouldn't be used
    fn build(&mut self) -> PyResult<TraceExporterPy> {
        Ok(TraceExporterPy {
            inner: self
                .builder
                .take()
                .ok_or(PyValueError::new_err("Builder has already been consumed"))?
                .build()
                .map_err(|err| PyValueError::new_err(format!("Builder {err}")))?,
        })
    }

    fn debug(&self) -> String {
        format!("{:?}", self.builder)
    }
}

/// A python object wrapping a [TraceExporter] instance
#[pyclass(
    name = "TraceExporter",
    module = "ddtrace.internal._native.data_pipeline"
)]
pub struct TraceExporterPy {
    inner: TraceExporter,
}

#[pymethods]
impl TraceExporterPy {
    /// Send a msgpack encoded trace payload.
    ///
    /// The payload is passed as a immutable byes object to be able to release the GIL while
    /// sending the traces.
    fn send(&self, py: Python<'_>, data: PyBackedBytes, trace_count: usize) -> PyResult<String> {
        py.allow_threads(move || {
            let slice: &[u8] = &data;
            let slice: &'static [u8] = unsafe { std::mem::transmute(slice) };
            match self.inner.send(Bytes::from_static(slice), trace_count) {
                Ok(res) => Ok(res.body),
                Err(e) => Err(exceptions::TraceExporterErrorPy::from(e).into()),
            }
        })
    }

    fn debug(&self) -> String {
        format!("{:?}", self.inner)
    }
}

#[pymodule]
pub fn register_data_pipeline(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TraceExporterBuilderPy>()?;
    m.add_class::<TraceExporterPy>()?;
    m.add_class::<TraceFormat>()?;
    exceptions::register_exceptions(m)?;

    Ok(())
}
