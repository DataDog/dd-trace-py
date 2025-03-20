use datadog_library_config::{Configurator, ProcessInfo};
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use pyo3::types::PyList;

#[pyclass(name = "PyConfigurator", module = "ddtrace.internal._native")]
pub struct PyConfigurator {
    configurator: Box<Configurator>,
    local_file: String,
    fleet_file: String,
}

#[pymethods]
impl PyConfigurator {
    #[new]
    pub fn new(debug_logs: bool) -> Self {
        PyConfigurator {
            configurator: Box::new(Configurator::new(debug_logs)),
            fleet_file: Configurator::FLEET_STABLE_CONFIGURATION_PATH.to_string(),
            local_file: Configurator::LOCAL_STABLE_CONFIGURATION_PATH.to_string(),
        }
    }

    pub fn set_local_file_override(&mut self, file: String) -> PyResult<()> {
        self.local_file = file;
        Ok(())
    }

    pub fn set_managed_file_override(&mut self, file: String) -> PyResult<()> {
        self.fleet_file = file;
        Ok(())
    }

    pub fn get_configuration(&self, py: Python<'_>) -> PyResult<PyObject> {
        let res_config = self.configurator.get_config_from_file(
            self.local_file.as_ref(),
            self.fleet_file.as_ref(),
            ProcessInfo::detect_global("python".to_string()),
        );
        match res_config {
            Ok(config) => {
                let list = PyList::empty(py);
                for c in config.iter() {
                    let dict = PyDict::new(py);
                    dict.set_item("name", c.name.to_str().to_owned())?;
                    dict.set_item("value", c.value.clone())?;
                    dict.set_item("source", c.source.to_str().to_owned())?;
                    dict.set_item("config_id", c.config_id.as_deref().unwrap_or("").to_owned())?;
                    list.append(dict)?;
                }
                Ok(list.into())
            }
            Err(e) => {
                let err_msg = format!("Failed to get configuration: {:?}", e);
                Err(PyException::new_err(err_msg))
            }
        }
    }
}
