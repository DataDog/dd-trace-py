"""Debug SCA integration with test agent."""

import json
import time
import uuid

from tests.appsec.appsec_utils import uvicorn_server
from tests.appsec.integrations.utils_testagent import _get_agent_client
from tests.appsec.integrations.utils_testagent import _get_span


# SCA environment
SCA_ENV = {
    "DD_APPSEC_SCA_ENABLED": "true",
    "DD_SCA_DETECTION_ENABLED": "true",
    "DD_APPSEC_ENABLED": "true",
    "DD_REMOTE_CONFIGURATION_ENABLED": "true",
    "DD_TRACE_DEBUG": "true",  # Enable debug logging
}

token = f"test_sca_debug_{uuid.uuid4()}"

print(f"Test token: {token}")

# Start uvicorn server
with uvicorn_server(
    appsec_enabled="true",
    token=token,
    port=8050,
    env=SCA_ENV,
) as context:
    _, fastapi_client, pid = context

    print("\n1. Server started, sending RC config...")

    # Send RC config
    path = "datadog/2/SCA_DETECTION/sca_config/config"
    msg = {"targets": ["os.path:join"]}

    client = _get_agent_client()
    client.request(
        "POST",
        f"/test/session/responses/config/path?test_session_token={token}",
        json.dumps({"path": path, "msg": msg}),
    )
    resp = client.getresponse()
    print(f"   RC config sent, response status: {resp.status}")
    print(f"   Response body: {resp.read()}")

    # Wait for RC to be processed
    print("\n2. Waiting for RC to be processed...")
    time.sleep(2)

    # Make request to endpoint
    print("\n3. Making HTTP request to /sca-vulnerable-function...")
    response = fastapi_client.get("/sca-vulnerable-function")
    print(f"   Response status: {response.status_code}")
    print(f"   Response body: {response.json()}")

    # Wait for spans to be sent
    time.sleep(1)

# Fetch spans
print("\n4. Fetching spans from test agent...")
response_tracer = _get_span(token)

print(f"   Total traces: {len(response_tracer)}")

for i, trace in enumerate(response_tracer):
    print(f"\n   Trace {i}:")
    for j, span in enumerate(trace):
        print(f"     Span {j}:")
        print(f"       name: {span.get('name')}")
        print(f"       resource: {span.get('resource')}")
        print(f"       service: {span.get('service')}")

        meta = span.get("meta", {})
        print(f"       meta keys: {list(meta.keys())}")

        # Check for SCA-related tags
        sca_tags = {k: v for k, v in meta.items() if "sca" in k.lower()}
        if sca_tags:
            print(f"       SCA tags found: {sca_tags}")
        else:
            print("       No SCA tags found")

        # Check for specific tags
        print(f"       _dd.sca.instrumented: {meta.get('_dd.sca.instrumented')}")
        print(f"       _dd.sca.detection_hit: {meta.get('_dd.sca.detection_hit')}")
        print(f"       _dd.sca.target: {meta.get('_dd.sca.target')}")

print("\n5. Test complete")
