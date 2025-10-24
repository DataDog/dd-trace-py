#!/bin/bash
#
# Helper script to run the Lock production stress test
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRESS_TEST_SCRIPT="$SCRIPT_DIR/lock_production_stress_test.py"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo "================================================================================"
echo "Lock Production Stress Test Runner"
echo "================================================================================"
echo ""

# Check which mode to use
if [ -n "$DD_API_KEY" ]; then
    echo -e "${GREEN}✓ Agentless mode detected${NC}"
    echo "  API Key: $(echo $DD_API_KEY | sed 's/./*/g')"
    echo "  Site: ${DD_SITE:-datadoghq.com}"
elif [ -n "$DD_AGENT_HOST" ]; then
    echo -e "${GREEN}✓ Agent mode detected${NC}"
    echo "  Agent Host: $DD_AGENT_HOST"
    echo "  Agent Port: ${DD_TRACE_AGENT_PORT:-8126}"
else
    echo -e "${RED}✗ No upload configuration detected!${NC}"
    echo ""
    echo "Please configure one of the following:"
    echo ""
    echo "Option 1: Agentless mode"
    echo "  export DD_API_KEY=\"your-api-key\""
    echo "  export DD_SITE=\"datadoghq.com\""
    echo ""
    echo "Option 2: Agent mode"
    echo "  export DD_AGENT_HOST=\"localhost\""
    echo "  export DD_TRACE_AGENT_PORT=\"8126\""
    echo ""
    exit 1
fi

echo ""
echo "Test Configuration:"
echo "  Duration: ${1:-120} seconds (${2:-8} workers)"
echo "  Service: lock-production-stress-test"
echo "  Environment: stress-test"
echo ""

DURATION=${1:-120}
WORKERS=${2:-8}

# Confirm before running
if [ "$3" != "--yes" ]; then
    echo -e "${YELLOW}This will run the stress test and upload profiling data to Datadog.${NC}"
    read -p "Continue? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 1
    fi
fi

echo ""
echo "================================================================================"
echo "Starting stress test..."
echo "================================================================================"
echo ""

# Run the stress test
python3 "$STRESS_TEST_SCRIPT" "$DURATION" "$WORKERS"

exit_code=$?

if [ $exit_code -eq 0 ]; then
    echo ""
    echo "================================================================================"
    echo -e "${GREEN}✓ Stress test completed successfully!${NC}"
    echo "================================================================================"
    echo ""
    echo "View profiling data at:"
    echo "  https://app.datadoghq.com/profiling"
    echo ""
    echo "Filter by:"
    echo "  Service: lock-production-stress-test"
    echo "  Environment: stress-test"
    echo ""
else
    echo ""
    echo "================================================================================"
    echo -e "${RED}✗ Stress test failed with exit code $exit_code${NC}"
    echo "================================================================================"
    echo ""
fi

exit $exit_code

