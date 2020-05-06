# -*- encoding: utf-8 -*-
import binascii
import datetime
import gzip
import os
import platform

import tenacity

from ddtrace.utils import deprecation
from ddtrace.utils.formats import parse_tags_str
from ddtrace.vendor import six
from ddtrace.vendor.six.moves import http_client
from ddtrace.vendor.six.moves.urllib import error
from ddtrace.vendor.six.moves.urllib import request

import ddtrace
from ddtrace.internal import runtime
from ddtrace.profiling import _attr
from ddtrace.profiling import _traceback
from ddtrace.profiling import exporter
from ddtrace.vendor import attr
from ddtrace.profiling.exporter import pprof


HOSTNAME = platform.node()
PYTHON_IMPLEMENTATION = platform.python_implementation().encode()
PYTHON_VERSION = platform.python_version().encode()


class InvalidEndpoint(exporter.ExportError):
    pass


class RequestFailed(exporter.ExportError):
    """Failed HTTP request."""

    def __init__(self, response, content):
        """Create a new failed request embedding response and content."""
        self.response = response
        self.content = content
        super(RequestFailed, self).__init__(
            "Error status code received from endpoint: %d: %s" % (response.status, content)
        )


class UploadFailed(exporter.ExportError):
    """Upload failure."""

    def __init__(self, exception):
        """Create a failed upload error based on raised exceptions."""
        self.exception = exception
        super(UploadFailed, self).__init__("Unable to upload: " + _traceback.format_exception(exception))


def _get_api_key():
    legacy = _attr.from_env("DD_PROFILING_API_KEY", "", str)()
    if legacy:
        deprecation.deprecation("DD_PROFILING_API_KEY", "Use DD_API_KEY")
        return legacy
    return _attr.from_env("DD_API_KEY", "", str)()


ENDPOINT_TEMPLATE = "https://intake.profile.{}/v1/input"


def _get_endpoint():
    legacy = _attr.from_env("DD_PROFILING_API_URL", "", str)()
    if legacy:
        deprecation.deprecation("DD_PROFILING_API_URL", "Use DD_SITE")
        return legacy
    site = _attr.from_env("DD_SITE", "datadoghq.com", str)()
    return ENDPOINT_TEMPLATE.format(site)


def _get_service_name():
    for service_name_var in ("DD_SERVICE", "DD_SERVICE_NAME", "DATADOG_SERVICE_NAME"):
        service_name = os.environ.get(service_name_var)
        if service_name is not None:
            return service_name


@attr.s
class PprofHTTPExporter(pprof.PprofExporter):
    """PProf HTTP exporter."""

    endpoint = attr.ib(factory=_get_endpoint, type=str)
    api_key = attr.ib(factory=_get_api_key, type=str)
    timeout = attr.ib(factory=_attr.from_env("DD_PROFILING_API_TIMEOUT", 10, float), type=float)
    service_name = attr.ib(factory=_get_service_name)
    max_retry_delay = attr.ib(default=None)

    def __attrs_post_init__(self):
        if self.max_retry_delay is None:
            self.max_retry_delay = self.timeout * 3

    @staticmethod
    def _encode_multipart_formdata(fields, tags):
        boundary = binascii.hexlify(os.urandom(16))

        # The body that is generated is very sensitive and must perfectly match what the server expects.
        body = (
            b"".join(
                b"--%s\r\n"
                b'Content-Disposition: form-data; name="%s"\r\n'
                b"\r\n"
                b"%s\r\n" % (boundary, field.encode(), value)
                for field, value in fields.items()
                if field != "chunk-data"
            )
            + b"".join(
                b"--%s\r\n"
                b'Content-Disposition: form-data; name="tags[]"\r\n'
                b"\r\n"
                b"%s:%s\r\n" % (boundary, tag.encode(), value)
                for tag, value in tags.items()
            )
            + b"--"
            + boundary
            + b"\r\n"
            b'Content-Disposition: form-data; name="chunk-data"; filename="profile.pb.gz"\r\n'
            + b"Content-Type: application/octet-stream\r\n\r\n"
            + fields["chunk-data"]
            + b"\r\n--%s--\r\n" % boundary
        )

        content_type = b"multipart/form-data; boundary=%s" % boundary

        return content_type, body

    @staticmethod
    def _get_tags(service):
        tags = {
            "service": service.encode("utf-8"),
            "host": HOSTNAME.encode("utf-8"),
            "runtime-id": runtime.get_runtime_id().encode("ascii"),
            "language": b"python",
            "runtime": PYTHON_IMPLEMENTATION,
            "runtime_version": PYTHON_VERSION,
            "profiler_version": ddtrace.__version__.encode("utf-8"),
        }

        version = os.environ.get("DD_VERSION")
        if version:
            tags["version"] = version.encode("utf-8")

        env = os.environ.get("DD_ENV")
        if env:
            tags["env"] = env.encode("utf-8")

        user_tags = parse_tags_str(os.environ.get("DD_TAGS", {}))
        user_tags.update(parse_tags_str(os.environ.get("DD_PROFILING_TAGS", {})))
        tags.update({k: six.ensure_binary(v) for k, v in user_tags.items()})
        return tags

    def export(self, events, start_time_ns, end_time_ns):
        """Export events to an HTTP endpoint.

        :param events: The event dictionary from a `ddtrace.profiling.recorder.Recorder`.
        :param start_time_ns: The start time of recording.
        :param end_time_ns: The end time of recording.
        """
        if not self.endpoint:
            raise InvalidEndpoint("Endpoint is empty")

        common_headers = {
            "DD-API-KEY": self.api_key.encode(),
        }

        profile = super(PprofHTTPExporter, self).export(events, start_time_ns, end_time_ns)
        s = six.BytesIO()
        with gzip.GzipFile(fileobj=s, mode="wb") as gz:
            gz.write(profile.SerializeToString())
        fields = {
            "runtime-id": runtime.get_runtime_id().encode("ascii"),
            "recording-start": (
                datetime.datetime.utcfromtimestamp(start_time_ns / 1e9).replace(microsecond=0).isoformat() + "Z"
            ).encode(),
            "recording-end": (
                datetime.datetime.utcfromtimestamp(end_time_ns / 1e9).replace(microsecond=0).isoformat() + "Z"
            ).encode(),
            "runtime": PYTHON_IMPLEMENTATION,
            "format": b"pprof",
            "type": b"cpu+alloc+exceptions",
            "chunk-data": s.getvalue(),
        }

        service_name = self.service_name or os.path.basename(profile.string_table[profile.mapping[0].filename])

        content_type, body = self._encode_multipart_formdata(fields, tags=self._get_tags(service_name),)
        headers = common_headers.copy()
        headers["Content-Type"] = content_type

        # urllib uses `POST` if `data` is supplied (Python 2 version does not handle `method` kwarg)
        req = request.Request(self.endpoint, data=body, headers=headers)

        retry = tenacity.Retrying(
            # Retry after 1s, 2s, 4s, 8s with some randomness
            wait=tenacity.wait_random_exponential(multiplier=0.5),
            stop=tenacity.stop_after_delay(self.max_retry_delay),
            retry=tenacity.retry_if_exception_type(
                (error.HTTPError, error.URLError, http_client.HTTPException, OSError, IOError)
            ),
        )

        try:
            retry(request.urlopen, req, timeout=self.timeout)
        except tenacity.RetryError as e:
            raise UploadFailed(e.last_attempt.exception())
