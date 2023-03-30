# -*- encoding: utf-8 -*-
import binascii
import datetime
import gzip
import itertools
import json
import os
import platform
import typing
from typing import Any
from typing import Dict

import attr
import six
from six.moves import http_client
import tenacity

import ddtrace
from ddtrace.ext.git import COMMIT_SHA
from ddtrace.ext.git import REPOSITORY_URL
from ddtrace.internal import agent
from ddtrace.internal import gitmetadata
from ddtrace.internal import runtime
from ddtrace.internal.processor.endpoint_call_counter import EndpointCallCounterProcessor
from ddtrace.internal.runtime import container
from ddtrace.internal.utils import attr as attr_utils
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.profiling import exporter
from ddtrace.profiling import recorder
from ddtrace.profiling.exporter import pprof


HOSTNAME = platform.node()
PYTHON_IMPLEMENTATION = platform.python_implementation()
PYTHON_VERSION = platform.python_version()


class UploadFailed(tenacity.RetryError, exporter.ExportError):
    """Upload failure."""

    def __str__(self):
        return str(self.last_attempt.exception())


@attr.s
class PprofHTTPExporter(pprof.PprofExporter):
    """PProf HTTP exporter."""

    # repeat this to please mypy
    enable_code_provenance = attr.ib(default=True, type=bool)

    endpoint = attr.ib(type=str, factory=agent.get_trace_url)
    api_key = attr.ib(default=None, type=typing.Optional[str])
    # Do not use the default agent timeout: it is too short, the agent is just a unbuffered proxy and the profiling
    # backend is not as fast as the tracer one.
    timeout = attr.ib(
        factory=attr_utils.from_env("DD_PROFILING_API_TIMEOUT", 10.0, float),
        type=float,
    )
    service = attr.ib(default=None, type=typing.Optional[str])
    env = attr.ib(default=None, type=typing.Optional[str])
    version = attr.ib(default=None, type=typing.Optional[str])
    tags = attr.ib(factory=dict, type=typing.Dict[str, str])
    max_retry_delay = attr.ib(default=None)
    _container_info = attr.ib(factory=container.get_container_info, repr=False)
    _retry_upload = attr.ib(init=False, eq=False)
    endpoint_path = attr.ib(default="/profiling/v1/input")

    endpoint_call_counter_span_processor = attr.ib(default=None, type=EndpointCallCounterProcessor)

    def _update_git_metadata_tags(self, tags):
        """
        Update profiler tags with git metadata
        """
        # clean tags, because values will be combined and inserted back in the same way as for tracer
        gitmetadata.clean_tags(tags)
        repository_url, commit_sha = gitmetadata.get_git_tags()
        if repository_url:
            tags[REPOSITORY_URL] = repository_url
        if commit_sha:
            tags[COMMIT_SHA] = commit_sha
        return tags

    def __attrs_post_init__(self):
        if self.max_retry_delay is None:
            self.max_retry_delay = self.timeout * 3
        self._retry_upload = tenacity.Retrying(
            # Retry after 1s, 2s, 4s, 8s with some randomness
            wait=tenacity.wait_random_exponential(multiplier=0.5),
            stop=tenacity.stop_after_delay(self.max_retry_delay),
            retry_error_cls=UploadFailed,
            retry=tenacity.retry_if_exception_type((http_client.HTTPException, OSError, IOError)),
        )
        tags = {
            k: six.ensure_str(v, "utf-8")
            for k, v in itertools.chain(
                self._update_git_metadata_tags(parse_tags_str(os.environ.get("DD_TAGS"))).items(),
                parse_tags_str(os.environ.get("DD_PROFILING_TAGS")).items(),
            )
        }
        tags.update(self.tags)
        tags.update(
            {
                "host": HOSTNAME,
                "language": "python",
                "runtime": PYTHON_IMPLEMENTATION,
                "runtime_version": PYTHON_VERSION,
                "profiler_version": ddtrace.__version__,
            }
        )
        if self.version:
            tags["version"] = self.version

        if self.env:
            tags["env"] = self.env

        self.tags = tags

    @staticmethod
    def _encode_multipart_formdata(
        event,  # type: bytes
        data,  # type: typing.List[typing.Dict[str, bytes]]
    ):
        # type: (...) -> typing.Tuple[bytes, bytes]
        boundary = binascii.hexlify(os.urandom(16))

        # The body that is generated is very sensitive and must perfectly match what the server expects.
        body = (
            (b"--%s\r\n" % boundary)
            + b'Content-Disposition: form-data; name="event"; filename="event.json"\r\n'
            + b"Content-Type: application/json\r\n\r\n"
            + event
            + b"\r\n"
            + b"".join(
                (b"--%s\r\n" % boundary)
                + (b'Content-Disposition: form-data; name="%s"; filename="%s"\r\n' % (item["name"], item["filename"]))
                + (b"Content-Type: %s\r\n\r\n" % (item["content-type"]))
                + item["data"]
                + b"\r\n"
                for item in data
            )
            + b"--%s--\r\n" % boundary
        )

        content_type = b"multipart/form-data; boundary=%s" % boundary

        return content_type, body

    def _get_tags(
        self, service  # type: str
    ):
        # type: (...) -> str
        tags = {
            "service": service,
            "runtime-id": runtime.get_runtime_id(),
        }

        tags.update(self.tags)

        return ",".join(tag + ":" + value for tag, value in tags.items())

    def export(
        self,
        events,  # type: recorder.EventsType
        start_time_ns,  # type: int
        end_time_ns,  # type: int
    ):
        # type: (...) -> typing.Tuple[pprof.pprof_ProfileType, typing.List[pprof.Package]]
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

        profile, libs = super(PprofHTTPExporter, self).export(events, start_time_ns, end_time_ns)
        pprof = six.BytesIO()
        with gzip.GzipFile(fileobj=pprof, mode="wb") as gz:
            gz.write(profile.SerializeToString())

        data = [
            {
                "name": b"auto",
                "filename": b"auto.pprof",
                "content-type": b"application/octet-stream",
                "data": pprof.getvalue(),
            }
        ]

        if self.enable_code_provenance:
            code_provenance = six.BytesIO()
            with gzip.GzipFile(fileobj=code_provenance, mode="wb") as gz:
                gz.write(
                    json.dumps(
                        {
                            "v1": libs,
                        }
                    ).encode("utf-8")
                )
            data.append(
                {
                    "name": b"code-provenance",
                    "filename": b"code-provenance.json",
                    "content-type": b"application/json",
                    "data": code_provenance.getvalue(),
                }
            )

        service = self.service or os.path.basename(profile.string_table[profile.mapping[0].filename])
        event = {
            "version": "4",
            "family": "python",
            "attachments": [item["filename"].decode("utf-8") for item in data],
            "tags_profiler": self._get_tags(service),
            "start": (datetime.datetime.utcfromtimestamp(start_time_ns / 1e9).replace(microsecond=0).isoformat() + "Z"),
            "end": (datetime.datetime.utcfromtimestamp(end_time_ns / 1e9).replace(microsecond=0).isoformat() + "Z"),
        }  # type: Dict[str, Any]

        if self.endpoint_call_counter_span_processor is not None:
            event["endpoint_counts"] = self.endpoint_call_counter_span_processor.reset()

        content_type, body = self._encode_multipart_formdata(
            event=json.dumps(event).encode("utf-8"),
            data=data,
        )
        headers["Content-Type"] = content_type

        client = agent.get_connection(self.endpoint, self.timeout)
        self._upload(client, self.endpoint_path, body, headers)

        return profile, libs

    def _upload(self, client, path, body, headers):
        self._retry_upload(self._upload_once, client, path, body, headers)

    def _upload_once(self, client, path, body, headers):
        try:
            client.request("POST", path, body=body, headers=headers)
            response = client.getresponse()
            response.read()  # reading is mandatory
        finally:
            client.close()

        if 200 <= response.status < 300:
            return

        if 500 <= response.status < 600:
            raise tenacity.TryAgain

        if response.status == 400:
            raise exporter.ExportError("Server returned 400, check your API key")
        elif response.status == 404 and not self.api_key:
            raise exporter.ExportError(
                "Datadog Agent is not accepting profiles. "
                "Agent-based profiling deployments require Datadog Agent >= 7.20"
            )

        raise exporter.ExportError("HTTP Error %d" % response.status)
