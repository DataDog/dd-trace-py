from .install import patch as _patch, patch_all as _patch_all
from .utils.deprecation import deprecated


@deprecated(message='Use ddtrace.install.patch_all instead', version='1.0.0')
def patch_all(**patch_modules):
    """Automatically patches all available modules.

    :param dict \**patch_modules: Override whether particular modules are patched or not.

        >>> patch_all(redis=False, cassandra=False)
    """
    _patch_all(**patch_modules)


@deprecated(message='Use ddtrace.install.patch instead', version='1.0.0')
def patch(raise_errors=True, **patch_modules):
    """Patch only a set of given modules.

    :param bool raise_errors: Raise error if one patch fail.
    :param dict \**patch_modules: List of modules to patch.

        >>> patch(psycopg=True, elasticsearch=True)
    """
    _patch(raise_errors=raise_errors, **patch_modules)
