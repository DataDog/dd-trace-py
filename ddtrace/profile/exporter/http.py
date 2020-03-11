import binascii
import datetime
import gzip
import logging
import os
import platform
import uuid

from ddtrace.vendor import six
from ddtrace.vendor.six.moves import http_client
from ddtrace.vendor.six.moves.urllib import parse as urlparse

import ddtrace
from ddtrace.profile import _traceback
from ddtrace.profile import exporter
from ddtrace.vendor import attr
from ddtrace.profile.exporter import pprof


LOG = logging.getLogger(__name__)


RUNTIME_ID = str(uuid.uuid4()).encode()
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

    def __init__(self, exceptions):
        """Create a failed upload error based on raised exceptions."""
        self.exceptions = exceptions
        super(UploadFailed, self).__init__(
            "Unable to upload: " + " , ".join(map(_traceback.format_exception, exceptions))
        )


@attr.s
class PprofHTTPExporter(pprof.PprofExporter):
    """PProf HTTP exporter."""

    endpoint = attr.ib(
        default=os.getenv("DD_PROFILING_API_URL", "https://intake.profile.datadoghq.com/v1/input"), type=str
    )
    api_key = attr.ib(default=os.getenv("DD_PROFILING_API_KEY", ""), type=str)
    timeout = attr.ib(default=os.getenv("DD_PROFILING_API_TIMEOUT", 10), type=float)

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
            "runtime-id": RUNTIME_ID,
            "language": b"python",
            "runtime": PYTHON_IMPLEMENTATION,
            "runtime_version": PYTHON_VERSION,
            "profiler_version": ddtrace.__version__.encode("utf-8"),
        }
        user_tags = os.getenv("DD_PROFILING_TAGS")
        if user_tags:
            for tag in user_tags.split(","):
                try:
                    key, value = tag.split(":", 1)
                except ValueError:
                    LOG.error("Malformed tag in DD_PROFILING_TAGS: %s", tag)
                else:
                    if isinstance(value, six.text_type):
                        value = value.encode("utf-8")
                    tags[key] = value
        return tags

    def export(self, events):
        """Export events to an HTTP endpoint.

        :param events: The event dictionary from a `ddtrace.profile.recorder.Recorder`.
        """
        if not self.endpoint:
            raise InvalidEndpoint("Endpoint is empty")
        parsed = urlparse.urlparse(self.endpoint)
        if parsed.scheme == "https":
            client_class = http_client.HTTPSConnection
        else:
            client_class = http_client.HTTPConnection
        if ":" in parsed.netloc:
            host, port = parsed.netloc.split(":", 1)
        else:
            host, port = parsed.netloc, None
        client = client_class(host, port, timeout=self.timeout)

        common_headers = {
            "DD-API-KEY": self.api_key.encode(),
        }

        exceptions = []
        profile = super(PprofHTTPExporter, self).export(events)
        s = six.BytesIO()
        with gzip.GzipFile(fileobj=s, mode="wb") as gz:
            gz.write(profile.SerializeToString())
        fields = {
            "runtime-id": RUNTIME_ID,
            "recording-start": (
                datetime.datetime.utcfromtimestamp(profile.time_nanos // 10e8).isoformat() + "Z"
            ).encode(),
            "recording-end": (datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z").encode(),
            "runtime": PYTHON_IMPLEMENTATION,
            "format": b"pprof",
            "type": b"cpu+alloc+exceptions",
            "chunk-data": s.getvalue(),
        }
        if "DD_SERVICE_NAME" in os.environ:
            service_name = os.environ.get("DD_SERVICE_NAME")
        elif "DATADOG_SERVICE_NAME" in os.environ:
            service_name = os.environ.get("DATADOG_SERVICE_NAME")
        else:
            service_name = os.path.basename(profile.string_table[profile.mapping[0].filename])
        content_type, body = self._encode_multipart_formdata(fields, tags=self._get_tags(service_name),)
        headers = common_headers.copy()
        headers["Content-Type"] = content_type
        try:
            client.request("POST", parsed.path, body=body, headers=headers)
        except (OSError, IOError, http_client.CannotSendRequest) as e:
            exceptions.append(e)
        else:
            try:
                response = client.getresponse()
                content = response.read()  # have to read to not fail!
            except (OSError, IOError, http_client.BadStatusLine) as e:
                exceptions.append(e)
            else:
                if not 200 <= response.status < 400:
                    exceptions.append(RequestFailed(response, content))

        if exceptions:
            raise UploadFailed(exceptions)
