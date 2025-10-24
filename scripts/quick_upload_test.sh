#!/bin/bash
#
# Quick test to verify profiling data uploads to Datadog
#

echo "================================================================================"
echo "Quick Profiling Upload Test"
echo "================================================================================"
echo ""

# Check configuration
echo "1. Checking upload configuration..."
if [ -n "$DD_AGENT_HOST" ]; then
    echo "   ✓ DD_AGENT_HOST: $DD_AGENT_HOST"
    echo "   ✓ DD_TRACE_AGENT_PORT: ${DD_TRACE_AGENT_PORT:-8126}"
    UPLOAD_MODE="agent"
    
    # Test agent connectivity
    echo ""
    echo "2. Testing agent connectivity..."
    AGENT_URL="http://${DD_AGENT_HOST}:${DD_TRACE_AGENT_PORT:-8126}/info"
    echo "   Trying: $AGENT_URL"
    
    if curl -s -f "$AGENT_URL" > /dev/null 2>&1; then
        echo "   ✓ Agent is reachable!"
    else
        echo "   ✗ Agent is NOT reachable!"
        echo ""
        echo "   Troubleshooting:"
        echo "   - Is the agent running?"
        echo "   - Try: docker ps | grep datadog"
        echo "   - Try: curl $AGENT_URL"
        echo ""
        exit 1
    fi
    
elif [ -n "$DD_API_KEY" ]; then
    echo "   ✓ DD_API_KEY: [hidden]"
    echo "   ✓ DD_SITE: ${DD_SITE:-datadoghq.com}"
    UPLOAD_MODE="agentless"
    
else
    echo "   ✗ NO UPLOAD CONFIGURATION!"
    echo ""
    echo "   You must set one of these:"
    echo ""
    echo "   Option 1: Local Agent"
    echo "     export DD_AGENT_HOST='localhost'"
    echo "     export DD_TRACE_AGENT_PORT='8126'  # optional"
    echo ""
    echo "   Option 2: Agentless"
    echo "     export DD_API_KEY='your-api-key'"
    echo "     export DD_SITE='datadoghq.com'"
    echo ""
    echo "   Then run this script again."
    echo ""
    exit 1
fi

echo ""
echo "3. Running quick profiling test..."
echo "   This will:"
echo "   - Enable lock profiling"
echo "   - Create and use test locks"
echo "   - Upload profile to Datadog"
echo ""

python3 << 'EOF'
import os
import threading
import time

# Configure profiler
os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_PROFILING_CAPTURE_PCT"] = "100"
os.environ["DD_SERVICE"] = "quick-upload-test"
os.environ["DD_ENV"] = "test"
os.environ["DD_VERSION"] = "1.0.0"

print("   - Starting profiler...")
import ddtrace.profiling.auto

print("   - Creating test locks...")
user_lock = threading.Lock()
cache_lock = threading.Lock()

print("   - Using locks (20 iterations)...")
for i in range(20):
    with user_lock:
        time.sleep(0.001)
    with cache_lock:
        time.sleep(0.001)

print("   - Forcing upload...")
from ddtrace.internal.datadog.profiling import ddup
try:
    ddup.upload()
    print("   ✓ Upload called successfully")
except Exception as e:
    print(f"   ✗ Upload error: {e}")
    exit(1)

print("   - Waiting 5 seconds...")
time.sleep(5)
print("   ✓ Test completed!")
EOF

if [ $? -eq 0 ]; then
    echo ""
    echo "================================================================================"
    echo "✓ Test Completed Successfully!"
    echo "================================================================================"
    echo ""
    echo "Next steps:"
    echo "1. Wait 2-3 minutes for data to appear in backend"
    echo "2. Go to: https://app.datadoghq.com/profiling"
    echo "3. Filter by:"
    echo "   - Service: quick-upload-test"
    echo "   - Environment: test"
    echo "4. Look for Lock profile samples"
    echo "5. Click on samples to see lock names (user_lock, cache_lock)"
    echo ""
    
    if [ "$UPLOAD_MODE" = "agent" ]; then
        echo "Uploaded via: Agent ($DD_AGENT_HOST:${DD_TRACE_AGENT_PORT:-8126})"
    else
        echo "Uploaded via: Agentless (${DD_SITE:-datadoghq.com})"
    fi
    
    echo ""
    echo "If data doesn't appear:"
    echo "- Check agent logs (if using agent mode)"
    echo "- Verify API key is valid (if using agentless)"
    echo "- Check for firewall/network issues"
    echo ""
else
    echo ""
    echo "================================================================================"
    echo "✗ Test Failed!"
    echo "================================================================================"
    echo ""
    echo "Check the error messages above."
    echo ""
fi

