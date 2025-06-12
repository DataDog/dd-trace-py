The Design of dd-trace-py
=========================

Parts of the Library
--------------------

When designing a change, one of the first decisions to make is where it should be made. This is an overview
of the main functional areas of the library.

A **product** is a unit of code within the library that implements functionality specific to a small set of
customer-facing Datadog products. Examples include the `appsec module <https://github.com/DataDog/dd-trace-py/tree/1.x/ddtrace/appsec>`_
implementing functionality for `App and API Protection <https://www.datadoghq.com/product/application-security-management/>`_
and the `profiling <https://github.com/DataDog/dd-trace-py/tree/1.x/ddtrace/profiling>`_ module implementing
functionality for `Continuous Profiling <https://docs.datadoghq.com/profiler/>`_. Ideally it only contains code
that is specific to the Datadog product being supported, and no code related to Integrations.

An **integration** is one of the modules in the `contrib <https://github.com/DataDog/dd-trace-py/tree/f26a526a6f79870e6e6a21d281f4796a434616bb/ddtrace/contrib>`_
directory, hooking our code into the internal logic of a given Python library. Ideally it only contains code
that is specific to the library being integrated with, and no code related to Products.

The **core** of the library is the abstraction layer that allows Products and Integrations to keep their concerns
separate. It is implemented in the Python files in the `top level of ddtracepy <https://github.com/DataDog/dd-trace-py/tree/main/ddtrace>`_
and in the ``internal`` module. As an implementation detail, the core logic also happens to directly support
`Application Performance Monitoring <https://docs.datadoghq.com/tracing/>`_.

Be mindful and intentional about which of these categories your change fits into, and avoid mixing concerns between
categories. If doing so requires more foundational refactoring or additional layers of abstraction, consider
opening an issue describing the limitations of the current design.


How Autoinstrumentation Works
-----------------------------

Autoinstrumentation is the feature of dd-trace-py that hooks into application code to facilitate application-level
product functionality, for example the detailed traces visible in Datadog Application Monitoring. This is an
overview of the technical implementation of this hooking functionality.

This is the series of steps involved in autoinstrumentation:

1. import bootstrap.sitecustomize, preload, clean up loaded modules, execute preexisting sitecustomizes
2. start products
3. set up an import hook for each integration
4. when an integrated-with module is imported, the import hook calls the contrib's patch(). patch() replaces important functions in the module with transparent wrappers.
5. These wrappers use the core API to create a tree of ExecutionContext objects. The context tree is traversed
   to generate the data to send to product intake.

Step 1: Bootstrap
-----------------

The autoinstrumentation entrypoint is ``import ddtrace.bootstrap.sitecustomize``. This can be done in user code, either directly or via
``import ddtrace.auto``, or behind the scenes by ``ddtrace-run``.

ddtrace's sitecustomize script's basic goal is to start the Products that the library implements.
These are subpackages like ``_trace``, ``profiling``, ``debugging``, ``appsec``, et cetera. Before starting these Products,
some setup has to happen, especially the execution of preexisting PEP648 ``sitecustomize`` scripts to maintain
user-facing guarantees about the runtime environment in which the application will execute. ddtrace's sitecustomize
also attempts to "leave no trace" on the runtime environment, especially by unloading all of the modules it has
used in the course of its setup phases. This helps reduce the possibility that ddtrace will use the same copy of
an integrated-with module that the application uses, which can lead to undefined behavior.

Step 2: Start Products
----------------------

The Products implemented by the library conform to a Product Manager Protocol, which controls common functionality
like setup, start, and stop. Each enabled Product module is loaded into the Manager instance on import, which happens in
sitecustomize. In turn, sitecustomize runs the Protocol with ``manager.run_protocol()`` and later runs additional Product
setup steps via ``preload.post_preload``. Together, these calls comprise the setup of Products that will operate during the
application's lifetime.

Step 3: Set Up Import Hooks
---------------------------

The core functionality set up during step 2 is autoinstrumentation, which is a prerequisite for many of the other Products.

Autoinstrumentation setup involves registering hooks that will execute when a particular module is imported. The list of
modules whose imports trigger this registration is defined in ``_monkey.py``. During this step, each of these modules has an
import hook registered for it. In the rest of this document, these modules are called "Instrumented Modules".

Note that as of this writing, autoinstrumentation is implemented as a side effect of the ``_trace`` Product's setup phase.
In a more abstract sense, autoinstrumentation can function as its own Product, and in the future may be refactored as such.

Step 4: Import Occurs
---------------------

The next step of autoinstrumentation happens when the application imports an Instrumented Module. The import triggers the
import hook that was registered in step 3. The function that the hook executes has the primary goal of calling the ``patch()``
function of the integration module located at ``ddtrace.contrib.<integration-name>.patch``.

The goal of an integration's ``patch()`` function is to invisibly wrap the Instrumented Module with logic that generates the
data about the module that's necessary for any relevant Products. The most commonly used wrapping approach is based on the
``wrapt`` library, which is included as a vendored dependency in ``ddtrace``. Another approach, employed by certain components of the appsec product, involves decompiling the imported module into its abstract syntax tree, modifying it, and then recompiling the module. Inside of the wrappers, it's common for
integrations to build trees of ``core.ExecutionContext`` objects that store information about the running application's
call stack.

Whatever approach is taken, this step only sets up logic that will run later.

Step 5: Application Logic Runs
------------------------------

When the application uses the Instrumented Module after importing it, the wrappers created in step 4 are executed. This causes
data about the running application to be collected in memory, often as a tree of ``ExecutionContext`` objects. Any Product
that was started in step 2 may access these data and use them to build a payload to send to the relevant intake endpoints.
The classic example is the ``_trace`` Product, which periodically traverses the context tree for the purpose of creating a ``Trace``
object that is subsequently serialized and sent to Datadog to power a flamegraph in the Application Observability product.
