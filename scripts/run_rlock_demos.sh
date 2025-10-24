#!/bin/bash
# Quick runner script for RLock profiling demos

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║           RLock Profiling Demo Suite                          ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Check for required environment variables (agentless mode)
if [ -z "$DD_API_KEY" ]; then
    echo "⚠️  DD_API_KEY not set!"
    echo ""
    echo "For agentless mode (simplest), set:"
    echo "  export DD_API_KEY=\"your-key\""
    echo "  export DD_SITE=\"datadoghq.com\"  # or datadoghq.eu"
    echo ""
    echo "Or to use local agent instead:"
    echo "  export DD_AGENT_HOST=\"localhost\""
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo "✅ DD_API_KEY is set (agentless mode)"
    if [ -n "$DD_SITE" ]; then
        echo "✅ DD_SITE = $DD_SITE"
    fi
fi
echo ""

# Menu
echo "Select a demo to run:"
echo ""
echo "  1) Simple Demo (30s) - Quick verification"
echo "  2) Heavy Contention (60s) - Maximum visibility"
echo "  3) Stress Test (60s, 10 workers) - Comprehensive"
echo "  4) Stress Test (120s, 20 workers) - Extended"
echo "  5) Stress Test (300s, 30 workers) - Marathon"
echo "  6) All Demos (run sequentially)"
echo ""
read -p "Enter choice [1-6]: " choice

case $choice in
    1)
        echo ""
        echo "Running Simple Demo..."
        python3 "$SCRIPT_DIR/rlock_simple_demo.py"
        ;;
    2)
        echo ""
        echo "Running Heavy Contention Demo..."
        python3 "$SCRIPT_DIR/rlock_heavy_contention.py"
        ;;
    3)
        echo ""
        echo "Running Stress Test (60s, 10 workers)..."
        python3 "$SCRIPT_DIR/rlock_stress_test.py" 60 10
        ;;
    4)
        echo ""
        echo "Running Stress Test (120s, 20 workers)..."
        python3 "$SCRIPT_DIR/rlock_stress_test.py" 120 20
        ;;
    5)
        echo ""
        echo "Running Stress Test (300s, 30 workers)..."
        python3 "$SCRIPT_DIR/rlock_stress_test.py" 300 30
        ;;
    6)
        echo ""
        echo "═════════════════════════════════════════════════════════════════"
        echo "Running ALL demos (this will take ~3 minutes)"
        echo "═════════════════════════════════════════════════════════════════"
        echo ""
        
        echo "▶ Demo 1/3: Simple Demo"
        python3 "$SCRIPT_DIR/rlock_simple_demo.py"
        
        echo ""
        echo "═════════════════════════════════════════════════════════════════"
        echo "▶ Demo 2/3: Heavy Contention"
        python3 "$SCRIPT_DIR/rlock_heavy_contention.py"
        
        echo ""
        echo "═════════════════════════════════════════════════════════════════"
        echo "▶ Demo 3/3: Stress Test"
        python3 "$SCRIPT_DIR/rlock_stress_test.py" 60 10
        
        echo ""
        echo "═════════════════════════════════════════════════════════════════"
        echo "✅ All demos completed!"
        echo "═════════════════════════════════════════════════════════════════"
        ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                     Demo Completed!                            ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "📊 View profiling data at:"
echo "   https://app.datadoghq.com/profiling"
echo ""
echo "🔍 Look for:"
echo "   • Lock acquire samples"
echo "   • Lock release samples (with hold duration)"
echo "   • Lock wait time samples (showing contention)"
echo "   • Reentrant lock patterns"
echo ""

