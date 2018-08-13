.. include:: ./shared.rst


.. _Installation:

Installation + Quickstart
=========================

Before installing be sure to read through the `setup documentation`_ to ensure
your environment is ready to receive traces.


Installation
------------

Install with :code:`pip`::

$ pip install ddtrace

We strongly suggest pinning the version of the library you deploy.

Quickstart
----------

Getting started with ``ddtrace`` is as easy as prefixing your python
entry-point command with ``ddtrace-run``.

For example if you start your application with ``python app.py`` then run::

  $ ddtrace-run python app.py

For more advanced usage of ``ddtrace-run`` refer to the documentation :ref:`here<ddtracerun>`.

OpenTracing
-----------

Coming soon!
