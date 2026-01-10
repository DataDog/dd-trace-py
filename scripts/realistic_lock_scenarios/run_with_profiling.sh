#!/bin/bash
#
# Run realistic lock scenarios with Lock Profiler and Otel Host Profiler
#
# Usage:
#   ./run_with_profiling.sh [scenario] [duration]
#
# Scenarios:
#   dogweb    - Django-like web app (default)
#   trace     - Trace agent simulator
#   profiler  - Profiler backend simulator
#   all       - Run all scenarios sequentially
#
# Prerequisites:
#   1. dd-trace-py installed with profiling support
#   2. dd-otel-host-profiler running (optional, for system-wide profiling)
#   3. Datadog Agent running (for sending profiles to Datadog)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIO="${1:-dogweb}"
DURATION="${2:-60}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Realistic Lock Scenario Profiling        ${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# Check if ddtrace is installed
if ! python -c "import ddtrace" 2>/dev/null; then
    echo -e "${RED}Error: ddtrace not installed${NC}"
    echo "Install with: pip install ddtrace"
    exit 1
fi

# Set common environment variables
export DD_PROFILING_ENABLED=true
export DD_PROFILING_LOCK_ENABLED=true
export DD_PROFILING_STACK_V2_ENABLED=true
export DD_PROFILING_ENABLE_ASSERTS=0  # Disable for production-like profiling

# Service-specific configuration
configure_service() {
    local scenario=$1
    case $scenario in
        dogweb)
            export DD_SERVICE="dogweb-simulator"
            export DD_ENV="profiling-test"
            export DD_VERSION="1.0.0"
            ;;
        trace)
            export DD_SERVICE="trace-agent-simulator"
            export DD_ENV="profiling-test"
            export DD_VERSION="1.0.0"
            ;;
        profiler)
            export DD_SERVICE="profiler-backend-simulator"
            export DD_ENV="profiling-test"
            export DD_VERSION="1.0.0"
            ;;
    esac
}

run_scenario() {
    local scenario=$1
    local duration=$2
    
    configure_service $scenario
    
    echo -e "${YELLOW}Running scenario: ${scenario}${NC}"
    echo -e "  Service: ${DD_SERVICE}"
    echo -e "  Duration: ${duration}s"
    echo -e "  Lock Profiling: ${DD_PROFILING_LOCK_ENABLED}"
    echo ""
    
    case $scenario in
        dogweb)
            ddtrace-run python "${SCRIPT_DIR}/dogweb_simulator.py" \
                --rps 100 \
                --duration "$duration" \
                --db-pools 3 \
                --pool-size 20 \
                --cache-clients 2 \
                --workers 4
            ;;
        trace)
            ddtrace-run python "${SCRIPT_DIR}/trace_agent_simulator.py" \
                --sps 1000 \
                --duration "$duration" \
                --buffers 10 \
                --processors 4
            ;;
        profiler)
            ddtrace-run python "${SCRIPT_DIR}/profiler_backend_simulator.py" \
                --pps 50 \
                --duration "$duration" \
                --workers 4
            ;;
        *)
            echo -e "${RED}Unknown scenario: ${scenario}${NC}"
            exit 1
            ;;
    esac
}

# Main execution
case $SCENARIO in
    all)
        echo -e "${GREEN}Running all scenarios...${NC}"
        echo ""
        run_scenario "dogweb" "$DURATION"
        echo ""
        run_scenario "trace" "$DURATION"
        echo ""
        run_scenario "profiler" "$DURATION"
        ;;
    dogweb|trace|profiler)
        run_scenario "$SCENARIO" "$DURATION"
        ;;
    *)
        echo -e "${RED}Unknown scenario: ${SCENARIO}${NC}"
        echo ""
        echo "Usage: $0 [dogweb|trace|profiler|all] [duration_seconds]"
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Profiling complete!                       ${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "View profiles in Datadog:"
echo "  https://app.datadoghq.com/profiling"
echo ""
echo "Filter by service:"
echo "  - dogweb-simulator"
echo "  - trace-agent-simulator"
echo "  - profiler-backend-simulator"

