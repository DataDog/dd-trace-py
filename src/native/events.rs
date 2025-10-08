/*!
# Event Hub Module

A high-performance event system for registering and calling listeners from both Python and Rust code.
Provides minimal-overhead event firing with automatic Python/Rust interoperability.

## Usage Examples

```rust
use crate::events::trace_span_started;

// Register a listener
let handle = trace_span_started::on(|span| {
    println!("Span started: {:?}", span);
});

// Fire event (only if listeners exist for performance)
if trace_span_started::has_listeners() {
    trace_span_started::dispatch(span_pyobject);
}

// Remove listener
trace_span_started::remove(handle);
```

```python
from ddtrace.internal.events import trace_span_started

def my_span_handler(span):
    print(f"Span started: {span}")

handle = trace_span_started.on(my_span_handler)
trace_span_started.dispatch(some_span)
trace_span_started.remove(handle)
```
*/

use pyo3::prelude::*;
use std::sync::LazyLock;
use std::sync::Mutex;
#[allow(unused_imports)] // Used in macro-generated code
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc, RwLock,
};

/// Unique identifier for event listeners
#[pyclass]
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ListenerHandle {
    pub id: u64,
    pub event_id: String,
}

#[pymethods]
impl ListenerHandle {
    fn __str__(&self) -> String {
        format!("ListenerHandle(id={}, event={})", self.id, self.event_id)
    }

    fn __repr__(&self) -> String {
        self.__str__()
    }
}

/// Type for Python registration functions collected at startup
pub type PythonRegistrationFn = Box<dyn Fn(&Bound<'_, PyModule>) -> PyResult<()> + Send + Sync>;

/// Global collection of Python registration functions
pub static PYTHON_REGISTRATIONS: LazyLock<Mutex<Vec<PythonRegistrationFn>>> =
    LazyLock::new(|| Mutex::new(Vec::new()));

/// Register a Python event registration function to be called during module initialization
#[allow(dead_code)]
pub fn register_python_event(registration_fn: PythonRegistrationFn) {
    PYTHON_REGISTRATIONS.lock().unwrap().push(registration_fn);
}

/// Create and register the events submodule with the parent module
pub fn register_events_module(parent_module: &Bound<'_, PyModule>) -> PyResult<()> {
    let events_module = PyModule::new(parent_module.py(), "events")?;
    events_module.add_class::<ListenerHandle>()?;

    // Register all events collected via ctor
    for registration_fn in PYTHON_REGISTRATIONS.lock().unwrap().iter() {
        registration_fn(&events_module)?;
    }

    parent_module.add_submodule(&events_module)?;
    Ok(())
}

/// Helper macro to generate the core event system (shared between full and native events)
#[macro_export]
macro_rules! define_event_core {
    (
        $event_name:ident,
        $listener_entry_type:ty,
        fn($($param:ident: $param_type:ty),*),
        $dispatch_impl:block
    ) => {
        /// Event-specific storage
        #[allow(dead_code)]
        static LISTENERS: LazyLock<RwLock<Vec<(u64, $listener_entry_type)>>> =
            LazyLock::new(|| RwLock::new(Vec::new()));
        #[allow(dead_code)]
        static NEXT_ID: AtomicU64 = AtomicU64::new(1);

        /// Generic function to add a listener
        #[allow(dead_code)]
        fn add_listener_internal(listener: $listener_entry_type) -> $crate::events::ListenerHandle {
            let id = NEXT_ID.fetch_add(1, Ordering::SeqCst);
            let handle = $crate::events::ListenerHandle {
                id,
                event_id: stringify!($event_name).to_string(),
            };

            LISTENERS.write().unwrap().push((id, listener));
            handle
        }

        /// Fire the event to all registered listeners
        #[allow(dead_code)]
        pub fn dispatch($($param: $param_type),*) $dispatch_impl

        /// Remove a listener by handle
        #[allow(dead_code)]
        pub fn remove(handle: $crate::events::ListenerHandle) -> bool {
            let mut listeners = LISTENERS.write().unwrap();
            if let Some(pos) = listeners.iter().position(|(id, _)| *id == handle.id) {
                listeners.remove(pos);
                return true;
            }
            false
        }

        /// Check if there are any listeners registered
        #[allow(dead_code)]
        pub fn has_listeners() -> bool {
            !LISTENERS.read().unwrap().is_empty()
        }
    };
}

/// Macro to define a full Python + Rust event
#[macro_export]
macro_rules! define_event {
    (
        $event_name:ident,
        fn($($param:ident: $param_type:ty),*),
        $python_sig:expr
    ) => {
        pub mod $event_name {
            use std::sync::{RwLock, Arc};
            use std::sync::atomic::{AtomicU64, Ordering};
            use std::sync::LazyLock;
            use pyo3::prelude::*;

            /// Listener entry for this specific event
            #[allow(dead_code)]
            pub enum ListenerEntry {
                Rust(Arc<dyn Fn($(&$param_type),*) + Send + Sync>),
                Python(PyObject),
            }

            $crate::define_event_core!(
                $event_name,
                ListenerEntry,
                fn($($param: &$param_type),*),
                {
                    let listeners = LISTENERS.read().unwrap();
                    for (_id, listener) in listeners.iter() {
                        match listener {
                            ListenerEntry::Rust(callback) => {
                                callback($($param),*);
                            }
                            ListenerEntry::Python(callback) => {
                                // Clone callback for Python invocation
                                let callback = callback.clone();
                                // Invoke Python callback with GIL
                                Python::with_gil(|py| {
                                    // Convert parameters to Python objects
                                    // Clone Py<PyAny>, convert primitives via IntoPyObject
                                    match callback.call1(py, ($($param.clone(),)*)) {
                                        Ok(_) => {},
                                        Err(e) => {
                                            // Print error but don't panic - other listeners should still run
                                            eprintln!("Error calling Python listener for {}: {:?}",
                                                stringify!($event_name), e);
                                        }
                                    }
                                });
                            }
                        }
                    }
                }
            );

            /// Register a Rust listener
            #[allow(dead_code)]
            pub fn on<F>(callback: F) -> $crate::events::ListenerHandle
            where
                F: Fn($(&$param_type),*) + Send + Sync + 'static,
            {
                add_listener_internal(ListenerEntry::Rust(Arc::new(callback)))
            }

            // Python integration - only compile when not running tests
            #[cfg(not(test))]
            mod python_integration {
                use super::*;

                /// Python wrapper for dispatch - receives owned values from Python
                #[pyfunction]
                pub fn dispatch_py($($param: $param_type),*) {
                    super::dispatch($(&$param),*);
                }

                /// Python wrapper for on (register listener)
                #[pyfunction]
                pub fn on_py(callback: PyObject) -> $crate::events::ListenerHandle {
                    super::add_listener_internal(ListenerEntry::Python(callback))
                }

                /// Python wrapper for remove
                #[pyfunction]
                pub fn remove_py(handle: $crate::events::ListenerHandle) -> bool {
                    super::remove(handle)
                }

                /// Python wrapper for has_listeners
                #[pyfunction]
                pub fn has_listeners_py() -> bool {
                    super::has_listeners()
                }
            }

            // Python integration with ctor - only compile when not running tests
            #[cfg(not(test))]
            #[ctor::ctor]
            fn register_python_object() {
                use python_integration::*;
                let registration_fn: $crate::events::PythonRegistrationFn = Box::new(|m: &Bound<'_, PyModule>| {
                    use pyo3::types::PyDict;

                    let py = m.py();
                    let event_name = stringify!($event_name);

                    // Create a SimpleNamespace with our PyO3 functions
                    let types_module = py.import("types")?;
                    let simple_namespace = types_module.getattr("SimpleNamespace")?;

                    let event_dict = PyDict::new(py);
                    event_dict.set_item("dispatch", wrap_pyfunction!(dispatch_py, m)?)?;
                    event_dict.set_item("on", wrap_pyfunction!(on_py, m)?)?;
                    event_dict.set_item("remove", wrap_pyfunction!(remove_py, m)?)?;
                    event_dict.set_item("has_listeners", wrap_pyfunction!(has_listeners_py, m)?)?;

                    // Call SimpleNamespace with kwargs (compatible with Python <3.13)
                    let event_obj = simple_namespace.call((), Some(&event_dict))?;
                    m.add(event_name, event_obj)?;

                    Ok(())
                });

                $crate::events::register_python_event(registration_fn);
            }
        }
    };
}

/// Macro to define a native-only (Rust-only) event
#[macro_export]
macro_rules! define_native_event {
    (
        $event_name:ident,
        fn($($param:ident: $param_type:ty),*)
    ) => {
        pub mod $event_name {
            use std::sync::{RwLock, Arc};
            use std::sync::atomic::{AtomicU64, Ordering};
            use std::sync::LazyLock;

            /// Listener entry for this native-only event
            #[allow(dead_code)]
            pub type ListenerEntry = Arc<dyn Fn($(&$param_type),*) + Send + Sync>;

            $crate::define_event_core!(
                $event_name,
                ListenerEntry,
                fn($($param: $param_type),*),
                {
                    let listeners = LISTENERS.read().unwrap();
                    for (_id, callback) in listeners.iter() {
                        callback($(&$param),*);
                    }
                }
            );

            /// Register a Rust listener
            #[allow(dead_code)]
            pub fn on<F>(callback: F) -> $crate::events::ListenerHandle
            where
                F: Fn($(&$param_type),*) + Send + Sync + 'static,
            {
                add_listener_internal(Arc::new(callback))
            }
        }
    };
}
