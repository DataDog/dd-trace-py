import typing as t
import warnings


class DDTraceDeprecationWarning(DeprecationWarning):
    # Override module to simplify adding warning filters by querying for
    # ddtrace.DDTraceDeprecationWarning but not have to expose this in the
    # public API. This also allows us to avoid circular imports that would occur if
    # it was contained in the top-level ddtrace package.
    __module__ = "ddtrace"


# generate_message() and deprecate() below are adapted from the OpenStack
# debtcollector project (Copyright (C) 2015 Yahoo! Inc.), previously vendored at
# ddtrace/vendor/debtcollector/_utils.py, licensed under the Apache License,
# Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0).
def generate_message(
    prefix: str,
    postfix: t.Optional[str] = None,
    message: t.Optional[str] = None,
    version: t.Optional[str] = None,
    removal_version: t.Optional[str] = None,
) -> str:
    """Generate a standardized deprecation message.

    :param prefix: prefix string used as the prefix of the output message
    :param postfix: postfix string appended to the output message
    :param message: additional message appended at the end of the output message
    :param version: version string this deprecation was introduced in
    :param removal_version: version string this deprecation will be removed in; ``"?"`` denotes
        an unknown future version
    """
    message_components = [prefix]
    if version:
        message_components.append(" in version '%s'" % version)
    if removal_version:
        if removal_version == "?":
            message_components.append(" and will be removed in a future version")
        else:
            message_components.append(" and will be removed in version '%s'" % removal_version)
    if postfix:
        message_components.append(postfix)
    if message:
        message_components.append(": %s" % message)
    return "".join(message_components)


def deprecate(
    prefix: str,
    postfix: t.Optional[str] = None,
    message: t.Optional[str] = None,
    version: t.Optional[str] = None,
    removal_version: t.Optional[str] = None,
    stacklevel: int = 3,
    category: type[DeprecationWarning] = DDTraceDeprecationWarning,
) -> None:
    """Emit a standardized deprecation warning.

    :param prefix: prefix string used as the prefix of the output message
    :param postfix: postfix string appended to the output message
    :param message: additional message appended at the end of the output message
    :param version: version string this deprecation was introduced in
    :param removal_version: version string this deprecation will be removed in; ``"?"`` denotes
        an unknown future version
    :param stacklevel: stacklevel passed to :func:`warnings.warn`
    :param category: the :mod:`warnings` category to use
    """
    out_message = generate_message(
        prefix, postfix=postfix, version=version, message=message, removal_version=removal_version
    )
    warnings.warn(out_message, category=category, stacklevel=stacklevel)
