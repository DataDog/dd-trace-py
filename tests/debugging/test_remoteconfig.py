import base64
import json

import httpretty
import mock

from ddtrace.debugging._config import DebuggerConfig
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._remoteconfig import DebuggingRCV07
from tests.utils import override_env


def test_rc_get_probes_with_mock_agent():
    with httpretty.enabled(), override_env(dict(DD_AGENT_PORT="9126")):
        config = DebuggerConfig()

        httpretty.register_uri(
            httpretty.POST,
            "%s/v0.7/config" % config.probe_url,
            body=json.dumps(
                {
                    "target_files": [
                        {
                            "raw": base64.b64encode(
                                json.dumps(
                                    {
                                        "id": "uwsgi",
                                        "orgId": 2,
                                        "snapshotProbes": [
                                            {
                                                "id": "59cbb288-8ecf-4443-ad0b-58d996965b64",
                                                "type": "snapshot",
                                                "created": 1635159911.8470528,
                                                "updated": None,
                                                "active": True,
                                                "orgId": 2,
                                                "appId": "uwsgi",
                                                "version": 0,
                                                "where": {
                                                    "typeName": None,
                                                    "methodName": None,
                                                    "sourceFile": "conduit/apps/articles/views.py",
                                                    "signature": None,
                                                    "lines": ["63"],
                                                },
                                                "when": None,
                                                "capture": None,
                                                "sampling": None,
                                                "language": "python",
                                                "tags": [],
                                                "userId": "a0ebf692-6c86-11eb-9598-da7ad0900001",
                                            },
                                            {
                                                "id": "ec1488e3-0a8a-47b0-a216-521defdaa6e8",
                                                "type": "snapshot",
                                                "created": 1624610018.808205,
                                                "updated": None,
                                                "active": True,
                                                "orgId": 2,
                                                "appId": "uwsgi",
                                                "version": 0,
                                                "where": {
                                                    "typeName": None,
                                                    "methodName": None,
                                                    "sourceFile": "conduit/apps/articles/views.py",
                                                    "signature": None,
                                                    "lines": ["66"],
                                                },
                                                "when": None,
                                                "capture": None,
                                                "sampling": None,
                                                "language": "java",
                                                "tags": [],
                                                "userId": "a0ebf692-6c86-11eb-9598-da7ad0900001",
                                            },
                                        ],
                                        "metricProbes": [
                                            {
                                                "id": "0b946993-619d-4582-af3c-d9227e8e82a9",
                                                "type": "metric",
                                                "created": 1639048863.0577338,
                                                "updated": None,
                                                "active": True,
                                                "orgId": 2,
                                                "service": "uwsgi",
                                                "version": 0,
                                                "where": {
                                                    "typeName": None,
                                                    "methodName": None,
                                                    "sourceFile": "foo/bar.py",
                                                    "signature": None,
                                                    "lines": ["42"],
                                                },
                                                "language": "java",
                                                "tags": [],
                                                "kind": "COUNT",
                                                "metricName": "python.test",
                                                "value": None,
                                                "userId": "a0ebf692-6c86-11eb-9598-da7ad0900001",
                                            }
                                        ],
                                        "allowList": None,
                                        "denyList": None,
                                        "sampling": None,
                                        "opsConfig": None,
                                    }
                                ).encode()
                            ).decode()
                        },
                        {
                            "raw": base64.b64encode(
                                json.dumps(
                                    {
                                        "id": "service2",
                                        "orgId": 2,
                                        "snapshotProbes": [
                                            {
                                                "language": "java",
                                                "type": "snapshot",
                                                "id": "probe3",
                                                "active": True,
                                                "where": {
                                                    "typeName": "java.lang.String",
                                                    "methodName": "concat",
                                                    "signature": "String (String)",
                                                },
                                                "capture": {
                                                    "maxReferenceDepth": 1,
                                                    "maxCollectionSize": 100,
                                                    "maxLength": 255,
                                                    "maxFieldDepth": -1,
                                                },
                                                "sampling": {"snapshotsPerSecond": 1.0},
                                                "tagMap": {},
                                            },
                                            {
                                                "language": "java",
                                                "id": "probe4",
                                                "type": "snapshot",
                                                "active": True,
                                                "where": {
                                                    "typeName": "java.lang.String",
                                                    "methodName": "toUpperCase",
                                                    "signature": "String ()",
                                                },
                                                "capture": {
                                                    "maxReferenceDepth": 1,
                                                    "maxCollectionSize": 100,
                                                    "maxLength": 255,
                                                    "maxFieldDepth": -1,
                                                },
                                                "sampling": {"snapshotsPerSecond": 1.0},
                                                "tagMap": {},
                                            },
                                        ],
                                    }
                                ).encode()
                            ).decode()
                        },
                    ]
                }
            ),
        )

        rc = DebuggingRCV07("uwsgi")

        probes = rc.get_probes()
        assert probes and all(isinstance(probe, Probe) for probe in probes)


@mock.patch("ddtrace.debugging._remoteconfig.log")
def test_rc_payload_size_limit_with_mock_agent(mock_log):
    with httpretty.enabled(), override_env(dict(DD_AGENT_PORT="9126")):
        config = DebuggerConfig()

        httpretty.register_uri(
            httpretty.POST,
            "%s/v0.7/config" % config.probe_url,
            body=json.dumps({"data": "a" * config.max_payload_size}),
        )

        DebuggingRCV07("uwsgi").get_probes()

        mock_log.error.assert_called_once_with(
            "Configuration payload size is too large (max: %d)", config.max_payload_size
        )
