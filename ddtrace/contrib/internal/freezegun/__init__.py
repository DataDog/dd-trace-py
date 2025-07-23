"""
The freezegun integration updates freezegun's default ignore list to ignore ddtrace.

Enabling
~~~~~~~~
The freezegun integration is enabled by default. Use :func:`patch()<ddtrace.patch>` to enable the integration::
    from ddtrace import patch
    patch(freezegun=True)


Configuration
~~~~~~~~~~~~~
The freezegun integration is not configurable, but may be disabled using DD_PATCH_MODULES=freezegun:false .
"""
