#!/usr/bin/env python3
"""
Manual test script for tracer flare functionality.
This script tests the flare feature against a real Datadog agent.
"""

import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid


# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.flare.flare import FlareSendRequest


def main():
    print("🧪 Manual Flare Test")
    print("=" * 50)

    # Configuration
    agent_url = "http://localhost:9126"
    flare_dir = "/tmp/tracer_flare_test"
    api_key = os.getenv("DD_API_KEY")

    print("🔧 Setting up flare test...")
    print(f"   Agent URL: {agent_url}")
    print(f"   Flare directory: {flare_dir}")
    print(f"   API Key: {api_key[:8]}...{api_key[-4:]}")

    # Clean up any existing test directory
    if os.path.exists(flare_dir):
        shutil.rmtree(flare_dir)

    # Create test directory
    os.makedirs(flare_dir, exist_ok=True)

    try:
        # Step 1: Prepare the flare
        print("\n📝 Step 1: Preparing flare (collecting logs and config)...")

        # Create ddconfig with required settings
        ddconfig = {
            "service": "flare-test-service",
            "env": "test",
            "version": "1.0.0",
            "_dd_api_key": api_key,
            "_dd_site": "datad0g.com",  # Use testing environment
        }

        flare = Flare(
            trace_agent_url=agent_url,
            ddconfig=ddconfig,
            api_key=api_key,
            timeout_sec=30,  # Longer timeout for real network calls
            flare_dir=flare_dir,
        )

        # Generate some test traces
        print("   Generating test traces...")
        from ddtrace import tracer

        with tracer.trace("test.operation", service="dd-trace-py") as span:
            span.set_tag("test.tag", "test_value")
            span.set_tag("component", "flare_test")
            time.sleep(0.1)

        # Prepare the flare
        flare.prepare("DEBUG")

        # Step 2: Check generated files
        print("\n📁 Step 2: Checking generated files...")
        config_json_path = None
        if os.path.exists(flare_dir):
            for file_path in Path(flare_dir).glob("*"):
                if file_path.is_file():
                    print(f"   ✅ {file_path.name} ({file_path.stat().st_size} bytes)")
                    if file_path.name.startswith("tracer_config_") and file_path.suffix == ".json":
                        config_json_path = file_path
        else:
            print("   ❌ Flare directory not found")
            return False

        # Print the JSON payload (tracer_config_{pid}.json)
        if config_json_path:
            print("\n📝 Flare JSON payload (tracer_config):")
            with open(config_json_path, "r") as f:
                try:
                    config_json = json.load(f)
                    print(json.dumps(config_json, indent=4))
                except Exception as e:
                    print(f"   ❌ Error reading JSON: {e}")
        else:
            print("   ❌ tracer_config_{pid}.json not found!")

        # Create flare request with UUID case_id
        # case_id = "0"  # Use 0 to create a new ticket instead of attaching to existing
        case_id = "23223"

        # Print sample AGENT_TASK config payload
        print("\n📝 Sample AGENT_TASK config payload:")
        agent_task_config = {
            "args": {
                "case_id": case_id,
                "hostname": "integration_tests",
                "user_handle": "paul.coignet@datadoghq.com",
            },
            "task_type": "tracer_flare",
            "uuid": str(uuid.uuid4()),
        }
        print(json.dumps(agent_task_config, indent=4))

        flare_request = FlareSendRequest(
            case_id=case_id,
            hostname="integration_tests",
            email="paul.coignet@datadoghq.com",
            uuid=str(uuid.uuid4()),  # Add UUID for race condition prevention
            source="tracer_python",
        )

        print("🚀 Step 3: Sending flare to Datadog...")
        print(f"   📧 Sending flare for case: {case_id}")
        print(f"   📧 Email: {flare_request.email}")
        print(f"   🖥️  Hostname: {flare_request.hostname}")
        print(f"   📧 Source: {flare_request.source}")

        # Print flare configuration details
        print("   ⚙️  Flare configuration:")
        print(f"      - Agent URL: {agent_url}")
        print(f"      - Site: {ddconfig.get('_dd_site', 'datadoghq.com')}")
        print(f"      - Flare directory: {flare_dir}")
        print(f"      - Timeout: {flare.timeout} seconds")

        # Print request details
        print("   📤 Request details:")
        print("      - Method: POST")
        print(f"      - URL: http://{ddconfig.get('_dd_site', 'datadoghq.com')}/tracer_flare/v1")
        print("      - Content-Type: multipart/form-data")
        print("      - Boundary: 83CAD6AA-8A24-462C-8B3D-FF9CC683B51B")
        print("      - Form fields: source, case_id, hostname, email, flare_file")
        print(f"      - Zip filename: tracer-python-{case_id}-{int(time.time() * 1000)}-debug.zip")

        # Print the actual payload (headers and body summary)
        print("\n   📨 Flare HTTP payload preview:")
        headers, body = flare._generate_payload(flare_request)
        print(f"      Debug: case_id = '{flare_request.case_id}'")
        for k, v in headers.items():
            print(f"      Header: {k}: {v}")
        print(f"      [binary zip content: {body.count(b'PK')}] bytes, total body size: {len(body)} bytes")

        # Send the flare
        try:
            flare.send(flare_request)
            print("   ✅ Flare sent successfully!")
        except Exception as e:
            print(f"   ❌ Error sending flare: {e}")
            return False
        finally:
            if os.path.exists(flare_dir):
                shutil.rmtree(flare_dir)

        print("\n" + "=" * 50)
        print("✅ Test completed successfully!")
        return True

    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
