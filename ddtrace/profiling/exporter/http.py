# -*- encoding: utf-8 -*-
import binascii
import datetime
import gzip
from http import client as http_client
import io
import itertools
import json
import os
import platform
import typing
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401

import ddtrace
from ddtrace.ext.git import COMMIT_SHA
from ddtrace.ext.git import MAIN_PACKAGE
from ddtrace.ext.git import REPOSITORY_URL
from ddtrace.internal import agent
from ddtrace.internal import compat
from ddtrace.internal import gitmetadata
from ddtrace.internal import runtime
from ddtrace.internal.processor.endpoint_call_counter import EndpointCallCounterProcessor
from ddtrace.internal.runtime import container
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter
from ddtrace.profiling import exporter
from ddtrace.profiling import recorder  # noqa:F401
from ddtrace.profiling.exporter import pprof
from ddtrace.settings.profiling import config


HOSTNAME = platform.node()
PYTHON_IMPLEMENTATION = platform.python_implementation()
PYTHON_VERSION = platform.python_version()


class PprofHTTPExporter(pprof.PprofExporter):
    """PProf HTTP exporter."""

    RETRY_ATTEMPTS = 3
    # List of attributes to ignore when comparing two exporters for equality
    # _upload is a function, which is dynamically added to the class, so we need
    # to ignore it in __eq__.
    EQ_IGNORE_ATTRS = ["_upload"]

    def __init__(
        self,
        enable_code_provenance: bool = True,
        endpoint: typing.Optional[str] = None,
        api_key: typing.Optional[str] = None,
        timeout: float = config.api_timeout,
        service: typing.Optional[str] = None,
        env: typing.Optional[str] = None,
        version: typing.Optional[str] = None,
        tags: typing.Optional[typing.Dict[str, str]] = None,
        max_retry_delay: typing.Optional[float] = None,
        endpoint_path: str = "/profiling/v1/input",
        endpoint_call_counter_span_processor: typing.Optional[EndpointCallCounterProcessor] = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        # repeat this to please mypy
        self.enable_code_provenance: bool = enable_code_provenance
        self.endpoint: str = endpoint if endpoint is not None else agent.get_trace_url()
        self.api_key: typing.Optional[str] = api_key
        # Do not use the default agent timeout: it is too short, the agent is just a unbuffered proxy and the profiling
        # backend is not as fast as the tracer one.
        self.timeout: float = timeout
        self.service: typing.Optional[str] = service
        self.env: typing.Optional[str] = env
        self.version: typing.Optional[str] = version
        self.tags: typing.Dict[str, str] = tags if tags is not None else {}
        self.max_retry_delay: typing.Optional[float] = max_retry_delay
        self._container_info: typing.Optional[container.CGroupInfo] = container.get_container_info()
        self.endpoint_path: str = endpoint_path
        self.endpoint_call_counter_span_processor: typing.Optional[
            EndpointCallCounterProcessor
        ] = endpoint_call_counter_span_processor

        self.__post_init__()

    def __eq__(self, other):
        # Exporter class used to be decorated with @attr.s which implements __eq__, using only the attributes defined
        # in the class. However, the _upload attribute is added dynamically to the class, so we need to ignore it when
        # comparing two exporters for equality.

        if isinstance(other, self.__class__):
            self_dict = {k: v for k, v in self.__dict__.items() if k not in self.EQ_IGNORE_ATTRS}
            other_dict = {k: v for k, v in other.__dict__.items() if k not in self.EQ_IGNORE_ATTRS}
            return self_dict == other_dict
        return False

    def _update_git_metadata_tags(self, tags):
        """
        Update profiler tags with git metadata
        """
        # clean tags, because values will be combined and inserted back in the same way as for tracer
        gitmetadata.clean_tags(tags)
        repository_url, commit_sha, main_package = gitmetadata.get_git_tags()
        if repository_url:
            tags[REPOSITORY_URL] = repository_url
        if commit_sha:
            tags[COMMIT_SHA] = commit_sha
        if main_package:
            tags[MAIN_PACKAGE] = main_package
        return tags

    def __post_init__(self):
        if self.max_retry_delay is None:
            self.max_retry_delay = self.timeout * 3

        self._upload = fibonacci_backoff_with_jitter(
            initial_wait=self.max_retry_delay / (1.618 ** (self.RETRY_ATTEMPTS - 1)),
            attempts=self.RETRY_ATTEMPTS,
        )(self._upload)

        tags = {
            k: compat.ensure_text(v, "utf-8")
            for k, v in itertools.chain(
                self._update_git_metadata_tags(parse_tags_str(os.environ.get("DD_TAGS"))).items(),
                config.tags.items(),
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
        self,
        service,  # type: str
    ):
        # type: (...) -> str
        tags = {
            "service": service,
            "runtime-id": runtime.get_runtime_id(),
        }

        tags.update(self.tags)

        return ",".join(f"{tag}:{value}" for tag, value in tags.items())

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

        container.update_headers_with_container_info(headers, self._container_info)

        profile, libs = super(PprofHTTPExporter, self).export(events, start_time_ns, end_time_ns)
        pprof = io.BytesIO()
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
            code_provenance = io.BytesIO()
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
                    "content-type": b"application/octet-stream",
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
            event["endpoint_counts"] = self.endpoint_call_counter_span_processor.reset()[0]

        content_type, body = self._encode_multipart_formdata(
            event=json.dumps(event).encode("utf-8"),
            data=data,
        )
        headers["Content-Type"] = content_type

        client = agent.get_connection(self.endpoint, self.timeout)
        self._upload(client, self.endpoint_path, body, headers)

        return profile, libs

    def _upload(self, client, path, body, headers):
        try:
            client.request("POST", path, body=body, headers=headers)
            response = client.getresponse()
            response.read()  # reading is mandatory
        except (http_client.HTTPException, EnvironmentError) as e:
            raise exporter.ExportError("HTTP upload request failed: %s" % e)
        finally:
            client.close()

        if 200 <= response.status < 300:
            return

        if 500 <= response.status < 600:
            raise RuntimeError("Server returned %d" % response.status)

        if response.status == 400:
            raise exporter.ExportError("Server returned 400, check your API key")
        elif response.status == 404 and not self.api_key:
            raise exporter.ExportError(
                "Datadog Agent is not accepting profiles. "
                "Agent-based profiling deployments require Datadog Agent >= 7.20"
            )

        raise exporter.ExportError("HTTP Error %d" % response.status)
