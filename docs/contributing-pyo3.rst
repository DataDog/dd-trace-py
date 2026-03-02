=========================================
Contributing: Rust/PyO3 Native Extensions
=========================================

Overview
--------

``dd-trace-py`` uses PyO3 to build Rust native extensions in ``src/native/``.
These compile to a single ``cdylib`` loaded as ``ddtrace.internal.native._native``.

Performance is critical — this code runs in production on every traced request.
Follow the rules below to avoid common performance pitfalls when writing or
reviewing Rust/PyO3 code.

Performance Rules
-----------------

Rule 1: Use ``#[pyclass(frozen)]`` for immutable data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

PyO3 maintains a runtime borrow checker using atomics for mutable pyclass types.
Every field access triggers an atomic compare-and-swap. Using ``frozen`` eliminates
this entirely — the compiler proves immutability at compile time.

.. code-block:: rust

    // GOOD: No runtime borrow checking overhead
    #[pyclass(frozen)]
    struct Config {
        #[pyo3(get)]
        name: String,
        #[pyo3(get)]
        value: i64,
    }

    // AVOID: Atomic borrow check on every field access
    #[pyclass]
    struct Config {
        #[pyo3(get)]
        name: String,
        #[pyo3(get)]
        value: i64,
    }

Use ``frozen`` for any class whose fields do not change after ``__new__``.


Rule 2: Use typed field references instead of ``Py<PyAny>``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When storing references to other PyO3 classes, use the concrete type.
``Py<PyAny>`` forces a runtime isinstance check on every access. Typed
references move that check to the function boundary (once) instead
of every access inside a loop.

.. code-block:: rust

    // GOOD: Type known at compile time, no per-access isinstance
    #[pyclass(frozen)]
    struct Node {
        next: Option<Py<Node>>,
    }

    // AVOID: isinstance check on every access
    #[pyclass(frozen)]
    struct Node {
        next: Option<Py<PyAny>>,
    }


Rule 3: Use ``Bound::get()`` instead of ``extract()`` for frozen+Sync classes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For ``#[pyclass(frozen)]`` types that implement ``Sync``, ``Bound::get()`` returns
``&T`` via a direct pointer dereference. ``extract()`` constructs a ``PyRef`` guard
with INCREF/DECREF and borrow tracking overhead.

.. code-block:: rust

    // GOOD: Direct pointer dereference for frozen Sync types
    let node: &Node = bound_node.get();

    // AVOID: Creates PyRef guard with refcount overhead
    let node: PyRef<Node> = bound_node.extract()?;


Rule 4: Use ``cast()`` over ``extract()`` for native PyO3 types
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For native types like ``PyString``, ``PyList``, etc., ``cast()`` is a simple pointer
operation. ``extract()`` internally calls ``cast()`` but also converts the error
from ``PyDowncastError`` to ``PyErr``, which involves allocation.

.. code-block:: rust

    // GOOD: Simple pointer cast, cheap error path
    if let Ok(s) = obj.cast::<PyString>() {
        // use s
    }

    // AVOID: Allocates PyErr on failure
    if let Ok(s) = obj.extract::<Bound<'_, PyString>>() {
        // use s
    }


Rule 5: Use ``#[inline(always)]`` on hot-path getters/setters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Property getters and setters on frequently-accessed pyclass types should
be inlined to eliminate function call overhead.

.. code-block:: rust

    #[pymethods]
    impl SpanData {
        #[getter]
        #[inline(always)]
        fn name(&self) -> &str {
            &self.name
        }

        #[setter]
        #[inline(always)]
        fn set_name(&mut self, value: String) {
            self.name = value;
        }
    }


Rule 6: Release the GIL for long Rust operations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``py.detach()`` when executing Rust code that does not interact with
Python objects. This is critical on GIL-enabled builds (allows other
threads) and important on free-threaded builds (avoids "stop the world"
pauses). Use for operations that take more than a few milliseconds.

.. code-block:: rust

    fn send(&self, py: Python<'_>, data: Vec<u8>) -> PyResult<String> {
        // GOOD: Release GIL during network I/O
        py.detach(move || {
            self.inner.send(&data)
                .map_err(|e| PyValueError::new_err(e.to_string()))
        })
    }

The closure passed to ``detach()`` must not access any Python objects.


Rule 7: Minimize data copies at the Python/Rust boundary
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Copying strings and bytes between Python and Rust involves allocation
and memcpy. When possible, borrow directly from the Python object's
memory rather than copying. Keep the Python object alive (via ``Py<T>``)
to prevent the GC from collecting it while Rust holds a pointer.

.. code-block:: rust

    // GOOD: Borrow the string data, keep Python object alive
    struct ZeroCopyStr {
        data: NonNull<str>,        // pointer into Python object memory
        _storage: Py<PyAny>,       // prevents GC collection
    }

    // AVOID: Copies the entire string into a new Rust allocation
    let s: String = py_string.extract()?;


Rule 8: Use Rust tuples for Python callable arguments
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

PyO3 dispatches Python calls using the ``vectorcall`` protocol when possible,
which is significantly faster. Only Rust tuples support ``vectorcall``;
``Bound<PyTuple>`` and ``Py<PyTuple>`` fall back to the slower ``tp_call``.

.. code-block:: rust

    // GOOD: Uses vectorcall
    callable.call1((arg1, arg2))?;

    // AVOID: Falls back to tp_call
    let args = PyTuple::new(py, &[arg1, arg2])?;
    callable.call1(args)?;


Rule 9: Use ``Bound::py()`` for token access
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When you already have a ``Bound<'py, T>`` reference, extract the Python
token from it for free. ``Python::attach()`` has overhead for checking
interpreter attachment status.

.. code-block:: rust

    // GOOD: Zero-cost token from existing bound reference
    let py = bound_obj.py();

    // AVOID: Unnecessary when a bound reference is available
    Python::attach(|py| { ... })


Rule 10: Batch operations to minimize boundary crossings
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each call across the Python/Rust boundary has FFI setup overhead.
Process collections in a single call rather than calling back and
forth per element.

.. code-block:: rust

    // GOOD: Process entire list in one boundary crossing
    #[pyfunction]
    fn process_items(items: Vec<String>) -> Vec<String> {
        items.into_iter().map(|s| transform(s)).collect()
    }

    // AVOID: Per-item boundary crossing from Python
    // for item in items:
    //     result.append(process_one(item))


Common Anti-Patterns
--------------------

- Do NOT store ``Bound<'py, T>`` in struct fields — it has a lifetime tied
  to the GIL. Use ``Py<T>`` for owned references that outlive a single call.
- Do NOT call Python functions inside tight Rust loops — batch operations
  or move the loop to the Python side.
- Do NOT hold the GIL during I/O — use ``py.detach()`` for any blocking work.
- Do NOT use ``Py<PyAny>`` when the concrete PyO3 type is known at compile time.
- Do NOT acquire the GIL when you already have a Python token from a bound reference.


References
----------

- `PyO3 Performance Guide <https://pyo3.rs/latest/performance.html>`_
- `Boundary Crossing Benchmarks <https://github.com/SonicField/boundary-crossing-bench>`_
- `PyO3 User Guide <https://pyo3.rs/latest/>`_
