The Design of dd-trace-py
=========================

Parts of the Library
--------------------

When designing a change, one of the first decisions to make is where it should be made. This is an overview
of the main functional areas of the library.

A **product** is a unit of code within the library that implements functionality specific to a small set of
customer-facing Datadog products. Examples include the `appsec module <https://github.com/DataDog/dd-trace-py/tree/1.x/ddtrace/appsec>`_
implementing functionality for `Application Security Management <https://www.datadoghq.com/product/application-security-management/>`_
and the `profiling <https://github.com/DataDog/dd-trace-py/tree/1.x/ddtrace/profiling>`_ module implementing
functionality for `Continuous Profiling <https://docs.datadoghq.com/profiler/>`_. Ideally it only contains code
that is specific to the Datadog product being supported, and no code related to Integrations.

An **integration** is one of the modules in the `contrib <https://github.com/DataDog/dd-trace-py/tree/f26a526a6f79870e6e6a21d281f4796a434616bb/ddtrace/contrib>`_
directory, hooking our code into the internal logic of a given Python library. Ideally it only contains code
that is specific to the library being integrated with, and no code related to Products.

The **core** of the library is the abstraction layer that allows Products and Integrations to keep their concerns
separate. It is implemented in the Python files in the `top level of ddtracepy <https://github.com/DataDog/dd-trace-py/tree/main/ddtrace>`_
and in the `internal` module. As an implementation detail, the core logic also happens to directly support
`Application Performance Monitoring <https://docs.datadoghq.com/tracing/>`_.

Be mindful and intentional about which of these categories your change fits into, and avoid mixing concerns between
categories. If doing so requires more foundational refactoring or additional layers of abstraction, consider
opening an issue describing the limitations of the current design.


How Autoinstrumentation Works
-----------------------------

Autoinstrumentation is the feature of dd-trace-py that hooks into application code to facilitate application-level
product functionality, for example the detailed traces visible in Datadog Application Observability. This is an
overview of the technical implementation of this hooking functionality.

This is the series of steps involved in autoinstrumentation:

1. import bootstrap.sitecustomize, preload, clean up loaded modules, execute preexisting sitecustomizes
2. start products
3. set up an import hook for each integration
4. when an integrated-with module is imported, the import hook calls the contrib's patch()
5. patch() replaces important functions in the module with transparent wrappers. These wrappers use the core API to create a
   tree of ExecutionContext objects. The context tree is traversed to generate the data to send to product intake.

Step 1: Bootstrap
-----------------

The autoinstrumentation entrypoint is `import ddtrace.bootstrap.sitecustomize`. This can be done in user code, either directly or via
`import ddtrace.auto`, or behind the scenes by `ddtrace-run`.

ddtrace's sitecustomize script's basic goal is to start the Products that the library implements.
These are subpackages like `_trace`, `profiling`, `debugging`, `appsec`, et cetera. Before starting these Products,
some setup has to happen, especially the execution of preexisting PEP648 `sitecustomize` scripts to maintain
user-facing guarantees about the runtime environment in which the application will execute. ddtrace's sitecustomize
also attempts to "leave no trace" on the runtime environment, especially by unloading all of the modules it has
used in the course of its setup phases. This helps reduce the possibility that ddtrace will use the same copy of
an integrated-with module that the application uses, which can lead to undefined behavior.

Step 2: Start Products
----------------------


Step 3: Set Up Import Hooks
---------------------------


Step 4: Import Occurs
---------------------


Step 5: Application Logic Runs
------------------------------
