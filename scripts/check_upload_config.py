#!/usr/bin/env python3
"""
Check if profiling data is being uploaded to Datadog
"""
import os
import sys
import time

print("="*80)
print("Datadog Upload Configuration Check")
print("="*80)

# Check environment variables
print("\n1. Environment Variables:")
upload_vars = {
    "DD_AGENT_HOST": os.environ.get("DD_AGENT_HOST"),
    "DD_TRACE_AGENT_PORT": os.environ.get("DD_TRACE_AGENT_PORT", "8126 (default)"),
    "DD_API_KEY": "***" if os.environ.get("DD_API_KEY") else None,
    "DD_SITE": os.environ.get("DD_SITE", "datadoghq.com (default)"),
    "DD_SERVICE": os.environ.get("DD_SERVICE"),
    "DD_ENV": os.environ.get("DD_ENV"),
    "DD_VERSION": os.environ.get("DD_VERSION"),
}

has_agent = upload_vars["DD_AGENT_HOST"] is not None
has_api_key = os.environ.get("DD_API_KEY") is not None

for key, val in upload_vars.items():
    status = "✓" if val and val != "None" else "✗"
    print(f"   {status} {key}: {val if val else '(not set)'}")

print("\n2. Upload Mode Detection:")
if has_agent:
    print(f"   ✓ AGENT MODE detected")
    print(f"     Will upload to: http://{upload_vars['DD_AGENT_HOST']}:{upload_vars['DD_TRACE_AGENT_PORT']}")
elif has_api_key:
    print(f"   ✓ AGENTLESS MODE detected")
    print(f"     Will upload to: {upload_vars['DD_SITE']}")
else:
    print(f"   ✗ NO UPLOAD CONFIGURATION!")
    print(f"     Profiles will NOT be uploaded to Datadog")
    print(f"\n   To fix, set one of:")
    print(f"     export DD_AGENT_HOST='localhost'")
    print(f"     OR")
    print(f"     export DD_API_KEY='your-key' DD_SITE='datadoghq.com'")

# Check agent connectivity if in agent mode
if has_agent:
    print("\n3. Testing Agent Connectivity:")
    try:
        import urllib.request
        import json
        
        agent_host = upload_vars["DD_AGENT_HOST"]
        agent_port = os.environ.get("DD_TRACE_AGENT_PORT", "8126")
        info_url = f"http://{agent_host}:{agent_port}/info"
        
        print(f"   Trying to connect to: {info_url}")
        
        try:
            response = urllib.request.urlopen(info_url, timeout=5)
            data = json.loads(response.read().decode())
            print(f"   ✓ Agent is reachable!")
            print(f"     Version: {data.get('version', 'unknown')}")
            
            # Check if profiling endpoint exists
            profiling_url = f"http://{agent_host}:{agent_port}/profiling/v1/input"
            print(f"\n   Checking profiling endpoint: {profiling_url}")
            
            # Just check if endpoint exists (will return 400 for GET but that's ok)
            req = urllib.request.Request(profiling_url, method='GET')
            try:
                urllib.request.urlopen(req, timeout=5)
                print(f"   ✓ Profiling endpoint exists")
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    print(f"   ✓ Profiling endpoint exists (returned 400 for GET, which is expected)")
                else:
                    print(f"   ⚠️  Profiling endpoint returned: {e.code}")
            
        except urllib.error.URLError as e:
            print(f"   ✗ Cannot reach agent: {e}")
            print(f"\n   Troubleshooting:")
            print(f"     - Is the agent running? Check: docker ps | grep datadog")
            print(f"     - Is the port correct? Default is 8126")
            print(f"     - Try: curl {info_url}")
            
    except Exception as e:
        print(f"   ⚠️  Error checking agent: {e}")

# Test actual upload
print("\n4. Testing Profile Upload:")
print("   Setting up profiler...")

os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_SERVICE"] = os.environ.get("DD_SERVICE", "upload-test")
os.environ["DD_ENV"] = os.environ.get("DD_ENV", "test")

import ddtrace.profiling.auto  # noqa: E402
import threading

print("   Creating and using test lock...")
test_lock = threading.Lock()
for i in range(20):
    with test_lock:
        time.sleep(0.001)

print("   Forcing upload...")
try:
    from ddtrace.internal.datadog.profiling import ddup
    ddup.upload()  # type: ignore
    print("   ✓ ddup.upload() called")
    
    print("\n   Waiting 5 seconds for upload to complete...")
    time.sleep(5)
    
    print("   ✓ Upload attempt completed")
    print("\n   Next steps:")
    print("   1. Wait 2-3 minutes")
    print("   2. Go to: https://app.datadoghq.com/profiling")
    print(f"   3. Filter by service: {os.environ.get('DD_SERVICE', 'upload-test')}")
    print(f"   4. Filter by env: {os.environ.get('DD_ENV', 'test')}")
    print("   5. Check if data appears")
    
    if not (has_agent or has_api_key):
        print("\n   ⚠️  WARNING: No upload configuration detected!")
        print("   Data will NOT appear in UI without DD_AGENT_HOST or DD_API_KEY")
    
except Exception as e:
    print(f"   ✗ Error during upload: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*80)
print("Configuration Check Complete")
print("="*80)

if not (has_agent or has_api_key):
    print("\n❌ PROBLEM: No upload configuration!")
    print("\nTo upload to Datadog, you MUST set one of:")
    print("\nOption 1: Local Agent")
    print("  export DD_AGENT_HOST='localhost'")
    print("  export DD_TRACE_AGENT_PORT='8126'  # optional, defaults to 8126")
    print("\nOption 2: Agentless (Direct to Datadog)")
    print("  export DD_API_KEY='your-datadog-api-key'")
    print("  export DD_SITE='datadoghq.com'  # or datadoghq.eu, us3.datadoghq.com, etc.")
    print("\nWithout these, profiles stay local only!")
elif has_agent:
    print("\n✓ Agent mode configured")
    print("  Make sure your agent is running and accessible")
elif has_api_key:
    print("\n✓ Agentless mode configured")
    print("  Make sure DD_SITE is correct for your region")

