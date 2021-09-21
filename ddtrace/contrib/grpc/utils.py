import logging

from ddtrace.internal.compat import parse

from . import constants


log = logging.getLogger(__name__)


def parse_method_path(method_path):
    """Returns (package, service, method) tuple from parsing method path"""
    # unpack method path based on "/{package}.{service}/{method}"
    # first remove leading "/" as unnecessary
    package_service, method_name = method_path.lstrip("/").rsplit("/", 1)

    # {package} is optional
    package_service = package_service.rsplit(".", 1)
    if len(package_service) == 2:
        return package_service[0], package_service[1], method_name

    return None, package_service[0], method_name


def set_grpc_method_meta(span, method, method_kind):
    method_path = method
    method_package, method_service, method_name = parse_method_path(method_path)
    if method_path is not None:
        span._set_str_tag(constants.GRPC_METHOD_PATH_KEY, method_path)
    if method_package is not None:
        span._set_str_tag(constants.GRPC_METHOD_PACKAGE_KEY, method_package)
    if method_service is not None:
        span._set_str_tag(constants.GRPC_METHOD_SERVICE_KEY, method_service)
    if method_name is not None:
        span._set_str_tag(constants.GRPC_METHOD_NAME_KEY, method_name)
    if method_kind is not None:
        span._set_str_tag(constants.GRPC_METHOD_KIND_KEY, method_kind)


def set_grpc_client_meta(span, host, port):
    if host:
        span._set_str_tag(constants.GRPC_HOST_KEY, host)
    if port:
        span._set_str_tag(constants.GRPC_PORT_KEY, str(port))
    span._set_str_tag(constants.GRPC_SPAN_KIND_KEY, constants.GRPC_SPAN_KIND_VALUE_CLIENT)


def _parse_target_from_args(args, kwargs):
    if "target" in kwargs:
        target = kwargs["target"]
    else:
        target = args[0]

    try:
        if target is None:
            return

        # ensure URI follows RFC 3986 and is preceded by double slash
        # https://tools.ietf.org/html/rfc3986#section-3.2
        parsed = parse.urlsplit("//" + target if not target.startswith("//") else target)
        port = None
        try:
            port = parsed.port
        except ValueError:
            log.warning("Non-integer port in target '%s'", target)

        # an empty hostname in Python 2.7 will be an empty string rather than
        # None
        hostname = parsed.hostname if parsed.hostname is not None and len(parsed.hostname) > 0 else None

        return hostname, port
    except ValueError:
        log.warning("Malformed target '%s'.", target)
