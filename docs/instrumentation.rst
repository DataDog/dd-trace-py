Basic Usage
===========

With ``ddtrace`` installed, the application can be instrumented.


Auto Instrumentation
--------------------

Python applications can easily be instrumented with ``ddtrace`` by using the
included ``ddtrace-run`` command. Simply prefix the Python execution command
with ``ddtrace-run`` in order to auto-instrument the libraries in the
application.

For example, if the command to the application run is::

$ python app.py

then to enable tracing, the corresponding command is::

$ ddtrace-run python app.py


Manual Instrumentation
----------------------

Manually instrumenting a Python application is as easy as::

  from ddtrace import patch_all
  patch_all()

**Note:** To ensure that the supported libraries are instrumented properly in
the application, they must be patched *prior* to importing them. So make sure to
call ``patch_all`` *before* importing libraries that are to be instrumented!

More information about ``patch_all`` is available in our `patch_all API
documentation`_.
