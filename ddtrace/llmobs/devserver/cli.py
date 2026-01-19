"""
CLI entry point for the LLMObs dev server.

Usage:
    ddtrace-llmobs-serve evaluators.py [--host HOST] [--port PORT] [--dev]
    ddtrace-llmobs-serve eval1.py eval2.py --port 8080
"""

import argparse
import sys
from pathlib import Path

from ddtrace.internal.logger import get_logger


logger = get_logger(__name__)


def main() -> int:
    """
    Main entry point for the CLI.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    parser = argparse.ArgumentParser(
        prog="ddtrace-llmobs-serve",
        description="Start the LLMObs dev server for remote evaluations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Start server with a single evaluator file
    ddtrace-llmobs-serve evaluators.py

    # Start with multiple files
    ddtrace-llmobs-serve eval1.py eval2.py

    # Custom host and port
    ddtrace-llmobs-serve evaluators.py --host 0.0.0.0 --port 9000

    # Development mode with auto-reload
    ddtrace-llmobs-serve evaluators.py --dev
""",
    )
    parser.add_argument(
        "files",
        nargs="+",
        metavar="FILE",
        help="Python files containing @evaluator decorated functions",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to bind to (default: 8080)",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Enable development mode with auto-reload",
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=100,
        dest="rate_limit",
        help="Requests per minute per API key (default: 100)",
    )

    args = parser.parse_args()

    # Validate files exist and are Python files
    for file_path in args.files:
        path = Path(file_path)
        if not path.exists():
            print(f"Error: File not found: {file_path}", file=sys.stderr)
            return 1
        if path.suffix != ".py":
            print(f"Error: Expected a Python file: {file_path}", file=sys.stderr)
            return 1

    # Import here to avoid loading uvicorn unless needed
    try:
        import uvicorn
    except ImportError:
        print(
            "Error: uvicorn is required for the dev server.\nInstall with: pip install uvicorn",
            file=sys.stderr,
        )
        return 1

    # Import registry and server
    from ddtrace.llmobs.devserver.registry import EvaluatorRegistry
    from ddtrace.llmobs.devserver.server import create_app

    # Scan all provided files for evaluators
    total_evaluators = 0
    for file_path in args.files:
        try:
            new_evaluators = EvaluatorRegistry.scan_module(file_path)
            total_evaluators += len(new_evaluators)
            print(f"Loaded {len(new_evaluators)} evaluator(s) from {file_path}: {new_evaluators}")
        except Exception as e:
            print(f"Error loading {file_path}: {e}", file=sys.stderr)
            return 1

    if total_evaluators == 0:
        print(
            "Warning: No evaluators found. Make sure your functions are decorated with @evaluator.",
            file=sys.stderr,
        )

    # Create and run the app
    app = create_app(requests_per_minute=args.rate_limit)

    mode = "Development (auto-reload enabled)" if args.dev else "Production"
    print(f"""
Starting LLMObs dev server...
  Host: {args.host}
  Port: {args.port}
  Evaluators: {total_evaluators}
  Rate limit: {args.rate_limit} req/min
  Mode: {mode}

Server URL: http://{args.host}:{args.port}
Press Ctrl+C to stop.
""")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.dev,
        log_level="info",
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
