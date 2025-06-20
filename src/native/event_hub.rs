use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};
use pyo3::wrap_pyfunction;
use std::collections::HashMap;
use std::sync::{Arc, RwLock};

/// High-performance event dispatcher implemented in Rust
/// 
/// Design principles for performance:
/// - Use Rust native types (HashMap, Vec, String) for internal storage
/// - Minimize Python object creation/conversion in hot paths
/// - Use RwLock for concurrent access (multiple readers, single writer)
/// - Cache listener lookups to avoid repeated HashMap access
/// - Store Python callbacks as PyObject pointers for zero-copy dispatch
#[pyclass(name = "EventHub")]
pub struct EventHub {
    /// Event-specific listeners: event_id -> {name -> callback}
    /// Using native Rust HashMap for O(1) lookup performance
    listeners: Arc<RwLock<HashMap<String, HashMap<String, PyObject>>>>,
    
    /// Global listeners that receive all events
    /// Stored as Vec for fast iteration during dispatch
    all_listeners: Arc<RwLock<Vec<PyObject>>>,
    
    /// Cached listener counts for fast has_listeners() checks
    /// This avoids read locks on the main HashMap for simple checks
    listener_counts: Arc<RwLock<HashMap<String, usize>>>,
}

#[pymethods]
impl EventHub {
    #[new]
    fn new() -> Self {
        Self {
            listeners: Arc::new(RwLock::new(HashMap::new())),
            all_listeners: Arc::new(RwLock::new(Vec::new())),
            listener_counts: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    /// Check if there are listeners for an event (O(1) operation)
    fn has_listeners(&self, event_id: &str) -> PyResult<bool> {
        // Fast path: check cached counts first
        let counts = self.listener_counts.read().unwrap();
        Ok(counts.get(event_id).map(|&count| count > 0).unwrap_or(false))
    }

    /// Register a listener for a specific event
    /// 
    /// Args:
    ///     event_id: Event identifier (stored as native Rust String)
    ///     callback: Python callable (stored as PyObject for zero-copy dispatch)
    ///     name: Optional name/ID for the callback (uses id() if None)
    #[pyo3(signature = (event_id, callback, name = None))]
    fn on(&self, event_id: String, callback: PyObject, name: Option<PyObject>) -> PyResult<()> {
        // Use callback's Python id() as name if not provided
        let callback_name = if let Some(name_obj) = name {
            Python::with_gil(|py| {
                // Convert name to string representation
                name_obj.bind(py).str().unwrap().to_string()
            })
        } else {
            // Use callback's id as string if no name provided
            Python::with_gil(|py| {
                // Try hash first, fall back to simple string representation of pointer
                let id_val = callback.bind(py).call_method0("__hash__").map(|h| h.extract::<i64>().unwrap_or(0))
                    .unwrap_or_else(|_| callback.as_ptr() as i64);
                id_val.to_string()
            })
        };

        {
            let mut listeners = self.listeners.write().unwrap();
            let event_listeners = listeners.entry(event_id.clone()).or_insert_with(HashMap::new);
            event_listeners.insert(callback_name, callback);
        }

        // Update cached count
        {
            let mut counts = self.listener_counts.write().unwrap();
            let listeners = self.listeners.read().unwrap();
            if let Some(event_listeners) = listeners.get(&event_id) {
                counts.insert(event_id, event_listeners.len());
            }
        }

        Ok(())
    }

    /// Register a global listener for all events
    fn on_all(&self, callback: PyObject) -> PyResult<()> {
        let mut all_listeners = self.all_listeners.write().unwrap();
        
        // Check if callback already exists to avoid duplicates
        let callback_id = Python::with_gil(|py| {
            callback.bind(py).call_method0("__hash__").unwrap_or_else(|_| {
                callback.bind(py).call_method0("__repr__").unwrap()
            }).extract::<u64>().unwrap_or(0)
        });
        
        // Simple duplicate check - could be optimized further with a HashSet
        let exists = all_listeners.iter().any(|existing| {
            Python::with_gil(|py| {
                let existing_id = existing.bind(py).call_method0("__hash__").unwrap_or_else(|_| {
                    existing.bind(py).call_method0("__repr__").unwrap()
                }).extract::<u64>().unwrap_or(1);
                existing_id == callback_id
            })
        });
        
        if !exists {
            all_listeners.insert(0, callback); // Insert at front like Python version
        }

        Ok(())
    }

    /// High-performance event dispatch - the critical hot path
    /// 
    /// This is optimized for minimal Python object creation:
    /// - Takes event_id as &str to avoid String allocation
    /// - Takes args as PyObject to handle any tuple type
    /// - Uses read locks for concurrent access
    /// - Calls Python callbacks directly without intermediate objects
    #[pyo3(signature = (event_id, args = None))]
    fn dispatch(&self, py: Python<'_>, event_id: &str, args: Option<PyObject>) -> PyResult<()> {
        let args = args.unwrap_or_else(|| PyTuple::empty(py).into());

        // Dispatch to global listeners first (fast Vec iteration)
        {
            let all_listeners = self.all_listeners.read().unwrap();
            for callback in all_listeners.iter() {
                // Create minimal tuple for global listeners: (event_id, args)
                let global_args = (event_id.to_string().into_py(py), args.clone_ref(py));
                
                if let Err(e) = callback.call1(py, global_args) {
                    // Check if we should raise exceptions based on config._raise
                    if should_raise_exceptions(py) {
                        return Err(e);
                    }
                    // Otherwise silently ignore exceptions in production
                }
            }
        }

        // Fast path: check if event has listeners before acquiring read lock
        {
            let counts = self.listener_counts.read().unwrap();
            if !counts.get(event_id).map(|&count| count > 0).unwrap_or(false) {
                return Ok(());
            }
        }

        // Dispatch to event-specific listeners
        {
            let listeners = self.listeners.read().unwrap();
            if let Some(event_listeners) = listeners.get(event_id) {
                for callback in event_listeners.values() {
                    // Convert args back to tuple for call1
                    let args_tuple = match args.downcast_bound::<PyTuple>(py) {
                        Ok(tuple) => tuple.clone(),
                        Err(_) => PyTuple::new(py, &[args.clone_ref(py)]).unwrap(),
                    };
                    
                    if let Err(e) = callback.call1(py, args_tuple) {
                        // Check if we should raise exceptions based on config._raise
                        if should_raise_exceptions(py) {
                            return Err(e);
                        }
                        // Otherwise silently ignore exceptions in production
                    }
                }
            }
        }

        Ok(())
    }

    /// Reset/clear listeners
    /// 
    /// Args:
    ///     event_id: If provided, only clear listeners for this event
    ///     callback: If provided, only remove this specific callback
    #[pyo3(signature = (event_id = None, callback = None))]
    fn reset(&self, event_id: Option<String>, callback: Option<PyObject>) -> PyResult<()> {
        match (event_id, callback) {
            (None, None) => {
                // Clear everything
                self.listeners.write().unwrap().clear();
                self.all_listeners.write().unwrap().clear();
                self.listener_counts.write().unwrap().clear();
            }
            (Some(event_id), None) => {
                // Clear specific event
                self.listeners.write().unwrap().remove(&event_id);
                self.listener_counts.write().unwrap().remove(&event_id);
            }
            (None, Some(callback)) => {
                // Remove callback from global listeners
                let mut all_listeners = self.all_listeners.write().unwrap();
                all_listeners.retain(|existing| {
                    Python::with_gil(|py| {
                        // Simple object comparison - check if they're the same Python object
                        !callback.bind(py).is(existing.bind(py))
                    })
                });
            }
            (Some(event_id), Some(callback)) => {
                // Remove specific callback from specific event
                let callback_name = Python::with_gil(|py| {
                    let id_val = callback.bind(py).call_method0("__hash__").map(|h| h.extract::<i64>().unwrap_or(0))
                        .unwrap_or_else(|_| callback.as_ptr() as i64);
                    id_val.to_string()
                });
                
                {
                    let mut listeners = self.listeners.write().unwrap();
                    if let Some(event_listeners) = listeners.get_mut(&event_id) {
                        event_listeners.retain(|name, _| name != &callback_name);
                        
                        // Update cached count
                        let mut counts = self.listener_counts.write().unwrap();
                        counts.insert(event_id, event_listeners.len());
                    }
                }
            }
        }
        
        Ok(())
    }

    /// Dispatch an event and collect individual results from each listener
    /// 
    /// This is the high-fidelity version of dispatch that returns results and exceptions
    /// from each individual listener. Used when the caller needs detailed feedback.
    #[pyo3(signature = (event_id, args = None))]
    fn dispatch_with_results(&self, py: Python<'_>, event_id: &str, args: Option<PyObject>) -> PyResult<EventResultDict> {
        let args = args.unwrap_or_else(|| PyTuple::empty(py).into());
        let mut results = EventResultDict::new(py);
        let should_raise = should_raise_exceptions(py);

        // Dispatch to global listeners first
        {
            let all_listeners = self.all_listeners.read().unwrap();
            for (idx, callback) in all_listeners.iter().enumerate() {
                let global_args = (event_id.to_string().into_py(py), args.clone_ref(py));
                let listener_name = format!("__global_listener_{}", idx);
                
                let result = match callback.call1(py, global_args) {
                    Ok(return_value) => EventResult::new(py, 0, Some(return_value), None), // RESULT_OK
                    Err(e) => {
                        if should_raise {
                            return Err(e);
                        }
                        EventResult::new(py, 1, None, Some(e.value(py).clone().unbind().into())) // RESULT_EXCEPTION
                    }
                };
                
                results.__setitem__(listener_name, result);
            }
        }

        // Dispatch to event-specific listeners
        {
            let listeners = self.listeners.read().unwrap();
            if let Some(event_listeners) = listeners.get(event_id) {
                for (name, callback) in event_listeners.iter() {
                    // Convert args back to tuple for call1
                    let args_tuple = match args.downcast_bound::<PyTuple>(py) {
                        Ok(tuple) => tuple.clone(),
                        Err(_) => PyTuple::new(py, &[args.clone_ref(py)]).unwrap(),
                    };
                    
                    let result = match callback.call1(py, args_tuple) {
                        Ok(return_value) => EventResult::new(py, 0, Some(return_value), None), // RESULT_OK
                        Err(e) => {
                            if should_raise {
                                return Err(e);
                            }
                            EventResult::new(py, 1, None, Some(e.value(py).clone().unbind().into())) // RESULT_EXCEPTION
                        }
                    };
                    
                    results.__setitem__(name.clone(), result);
                }
            }
        }

        Ok(results)
    }

    /// Get statistics about the event hub (useful for debugging/monitoring)
    fn stats(&self, py: Python<'_>) -> PyResult<PyObject> {
        let listeners_count = self.listeners.read().unwrap().len();
        let all_listeners_count = self.all_listeners.read().unwrap().len();
        
        let total_event_listeners: usize = self.listeners.read().unwrap()
            .values()
            .map(|event_listeners| event_listeners.len())
            .sum();

        let stats = PyDict::new(py);
        stats.set_item("events_with_listeners", listeners_count)?;
        stats.set_item("global_listeners", all_listeners_count)?;
        stats.set_item("total_event_listeners", total_event_listeners)?;
        
        Ok(stats.into())
    }
}

/// Result types for dispatch_with_results functionality
#[pyclass(name = "ResultType")]
pub struct ResultType;

#[pymethods]
impl ResultType {
    #[classattr]
    const RESULT_OK: i32 = 0;
    #[classattr]
    const RESULT_EXCEPTION: i32 = 1;
    #[classattr]
    const RESULT_UNDEFINED: i32 = -1;
}

/// EventResult for individual listener results
#[pyclass(name = "EventResult")]
pub struct EventResult {
    #[pyo3(get, set)]
    pub response_type: i32,
    #[pyo3(get, set)]
    pub value: PyObject,
    #[pyo3(get, set)]
    pub exception: Option<PyObject>,
}

impl Clone for EventResult {
    fn clone(&self) -> Self {
        Python::with_gil(|py| {
            Self {
                response_type: self.response_type,
                value: self.value.clone_ref(py),
                exception: self.exception.as_ref().map(|e| e.clone_ref(py)),
            }
        })
    }
}

#[pymethods]
impl EventResult {
    #[new]
    #[pyo3(signature = (response_type = -1, value = None, exception = None))]
    fn new(py: Python<'_>, response_type: i32, value: Option<PyObject>, exception: Option<PyObject>) -> Self {
        Self {
            response_type,
            value: value.unwrap_or_else(|| py.None()),
            exception,
        }
    }

    fn __bool__(&self) -> bool {
        self.response_type == 0 // RESULT_OK
    }
}

/// EventResultDict for holding results with attribute access and missing key behavior
#[pyclass(name = "EventResultDict", subclass)]
pub struct EventResultDict {
    results: HashMap<String, EventResult>,
    missing_result: EventResult,
}

#[pymethods]
impl EventResultDict {
    #[new]
    fn new(py: Python<'_>) -> Self {
        Self {
            results: HashMap::new(),
            missing_result: EventResult::new(py, -1, None, None), // RESULT_UNDEFINED
        }
    }

    fn __getitem__(&self, key: &str) -> EventResult {
        self.results.get(key).cloned().unwrap_or_else(|| self.missing_result.clone())
    }

    fn __setitem__(&mut self, key: String, value: EventResult) {
        self.results.insert(key, value);
    }

    fn __getattr__(&self, name: &str) -> EventResult {
        self.__getitem__(name)
    }

    fn __contains__(&self, key: &str) -> bool {
        self.results.contains_key(key)
    }

    fn keys(&self) -> Vec<String> {
        self.results.keys().cloned().collect()
    }

    fn values(&self) -> Vec<EventResult> {
        self.results.values().cloned().collect()
    }

    fn items(&self) -> Vec<(String, EventResult)> {
        self.results.iter().map(|(k, v)| (k.clone(), v.clone())).collect()
    }
}

/// Helper function to check if we should raise exceptions based on config._raise
fn should_raise_exceptions(py: Python<'_>) -> bool {
    // Try to import and check config._raise
    match py.import("ddtrace.settings._config") {
        Ok(config_module) => {
            match config_module.getattr("config") {
                Ok(config_obj) => {
                    match config_obj.getattr("_raise") {
                        Ok(raise_val) => raise_val.is_truthy().unwrap_or(false),
                        Err(_) => false,
                    }
                }
                Err(_) => false,
            }
        }
        Err(_) => false,
    }
}

/// Create a global instance for backward compatibility with the Python module
/// This allows for both instance-based and module-level usage
static GLOBAL_HUB: std::sync::OnceLock<EventHub> = std::sync::OnceLock::new();

/// Module-level functions for backward compatibility with Python code
#[pyfunction]
fn has_listeners(event_id: &str) -> PyResult<bool> {
    let hub = GLOBAL_HUB.get_or_init(EventHub::new);
    hub.has_listeners(event_id)
}

#[pyfunction]
#[pyo3(signature = (event_id, callback, name = None))]
fn on(event_id: String, callback: PyObject, name: Option<PyObject>) -> PyResult<()> {
    let hub = GLOBAL_HUB.get_or_init(EventHub::new);
    hub.on(event_id, callback, name)
}

#[pyfunction]
fn on_all(callback: PyObject) -> PyResult<()> {
    let hub = GLOBAL_HUB.get_or_init(EventHub::new);
    hub.on_all(callback)
}

#[pyfunction]
#[pyo3(signature = (event_id, args = None))]
fn dispatch(py: Python<'_>, event_id: &str, args: Option<PyObject>) -> PyResult<()> {
    let hub = GLOBAL_HUB.get_or_init(EventHub::new);
    hub.dispatch(py, event_id, args)
}

#[pyfunction]
#[pyo3(signature = (event_id = None, callback = None))]
fn reset(event_id: Option<String>, callback: Option<PyObject>) -> PyResult<()> {
    let hub = GLOBAL_HUB.get_or_init(EventHub::new);
    hub.reset(event_id, callback)
}

#[pyfunction]
#[pyo3(signature = (event_id, args = None))]
fn dispatch_with_results(py: Python<'_>, event_id: &str, args: Option<PyObject>) -> PyResult<EventResultDict> {
    let hub = GLOBAL_HUB.get_or_init(EventHub::new);
    hub.dispatch_with_results(py, event_id, args)
}

/// Create the event hub Python module
#[pymodule]
pub fn event_hub(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<EventHub>()?;
    m.add_class::<EventResult>()?;
    m.add_class::<EventResultDict>()?;
    m.add_class::<ResultType>()?;
    m.add_function(wrap_pyfunction!(has_listeners, m)?)?;
    m.add_function(wrap_pyfunction!(on, m)?)?;
    m.add_function(wrap_pyfunction!(on_all, m)?)?;
    m.add_function(wrap_pyfunction!(dispatch, m)?)?;
    m.add_function(wrap_pyfunction!(dispatch_with_results, m)?)?;
    m.add_function(wrap_pyfunction!(reset, m)?)?;
    Ok(())
}

