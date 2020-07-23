# -*- encoding: utf-8 -*-
import binascii
import datetime
import gzip
import os
import platform

import tenacity

from ddtrace.internal.runtime import container
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


class UploadFailed(exporter.ExportError):
    """Upload failure."""

    def __init__(self, exception, msg=None):
        """Create a failed upload error based on raised exceptions."""
        self.exception = exception
        msg = _traceback.format_exception(exception) if msg is None else msg
        super(UploadFailed, self).__init__("Unable to upload profile: " + msg)


@attr.s
class PprofHTTPExporter(pprof.PprofExporter):
    """PProf HTTP exporter."""

    endpoint = attr.ib()
    api_key = attr.ib(default=None)
    timeout = attr.ib(factory=_attr.from_env("DD_PROFILING_API_TIMEOUT", 10, float), type=float)
    service = attr.ib(default=None)
    env = attr.ib(default=None)
    version = attr.ib(default=None)
    max_retry_delay = attr.ib(default=None)
    _container_info = attr.ib(factory=container.get_container_info, repr=False)

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

    def _get_tags(self, service):
        tags = {
            "service": service.encode("utf-8"),
            "host": HOSTNAME.encode("utf-8"),
            "runtime-id": runtime.get_runtime_id().encode("ascii"),
            "language": b"python",
            "runtime": PYTHON_IMPLEMENTATION,
            "runtime_version": PYTHON_VERSION,
            "profiler_version": ddtrace.__version__.encode("utf-8"),
        }

        if self.version:
            tags["version"] = self.version.encode("utf-8")

        if self.env:
            tags["env"] = self.env.encode("utf-8")

        user_tags = parse_tags_str(os.environ.get("DD_TAGS", {}))
        user_tags.update(parse_tags_str(os.environ.get("DD_PROFILING_TAGS", {})))
        tags.update({k: six.ensure_binary(v) for k, v in user_tags.items()})
        return tags

    @staticmethod
    def _retry_on_http_5xx(exception):
        if isinstance(exception, error.HTTPError):
            return 500 <= exception.code < 600
        return True

    def export(self, events, start_time_ns, end_time_ns):
        """Export events to an HTTP endpoint.

        :param events: The event dictionary from a `ddtrace.profiling.recorder.Recorder`.
        :param start_time_ns: The start time of recording.
        :param end_time_ns: The end time of recording.
        """
        if self.api_key:
            headers = {
                "DD-API-KEY": self.api_key.encode(),
            }
        else:
            headers = {}

        if self._container_info and self._container_info.container_id:
            headers["Datadog-Container-Id"] = self._container_info.container_id

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

        service = self.service or os.path.basename(profile.string_table[profile.mapping[0].filename])

        content_type, body = self._encode_multipart_formdata(fields, tags=self._get_tags(service),)
        headers["Content-Type"] = content_type

        # urllib uses `POST` if `data` is supplied (Python 2 version does not handle `method` kwarg)
        req = request.Request(self.endpoint, data=body, headers=headers)

        retry = tenacity.Retrying(
            # Retry after 1s, 2s, 4s, 8s with some randomness
            wait=tenacity.wait_random_exponential(multiplier=0.5),
            stop=tenacity.stop_after_delay(self.max_retry_delay),
            retry=tenacity.retry_if_exception_type((http_client.HTTPException, OSError, IOError))
            & tenacity.retry_if_exception(self._retry_on_http_5xx),
        )

        try:
            retry(request.urlopen, req, timeout=self.timeout)
        except tenacity.RetryError as e:
            raise UploadFailed(e.last_attempt.exception())
        except error.HTTPError as e:
            if e.code == 400:
                msg = "Server returned 400, check your API key"
            elif e.code == 404 and not self.api_key:
                msg = (
                    "Datadog Agent is not accepting profiles. "
                    "Agent-based profiling deployments require Datadog Agent >= 7.20"
                )
            else:
                msg = None
            raise UploadFailed(e, msg)
