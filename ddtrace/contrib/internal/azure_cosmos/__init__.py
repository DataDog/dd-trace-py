"""
The azure_cosmos integration instruments the CRUD operations of the
Azure CosmosDB library.


Enabling
~~~~~~~~

The azure_cosmos integration is enabled automatically when using :ref:`import ddtrace.auto <ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(azure_cosmos=True)



"""
