"""
WSGI application for testing runtime coverage collection.

This app exercises code from tests/coverage/included_path to generate
coverage data when DD_TRACE_RUNTIME_COVERAGE_ENABLED is set.

Can be run directly with: ddtrace-run python tests/ci_visibility/app_with_runtime_coverage.py [port]
"""

import sys
import threading
import time


def application(environ, start_response):
    """WSGI application that exercises instrumented code."""
    path = environ.get("PATH_INFO", "/")

    if path == "/":
        # Exercise multiple functions to generate coverage
        from tests.coverage.included_path.callee import called_in_context_main
        from tests.coverage.included_path.callee import called_in_session_main

        result1 = called_in_context_main(1, 2)
        result2 = called_in_session_main(3, 4)
        response_body = f"Results: {result1}, {result2}".encode("utf-8")
        status = "200 OK"
    elif path == "/shutdown":
        from ddtrace import tracer

        tracer.shutdown()
        response_body = b"Shutting down"
        status = "200 OK"

        def delayed_exit():
            time.sleep(0.1)
            sys.exit(0)

        threading.Thread(target=delayed_exit, daemon=True).start()
    else:
        response_body = b"Not Found"
        status = "404 Not Found"

    headers = [("Content-Type", "text/plain"), ("Content-Length", str(len(response_body)))]
    start_response(status, headers)
    return [response_body]


if __name__ == "__main__":
    from wsgiref.simple_server import make_server, WSGIServer
    import socket

    # Custom server class that sets SO_REUSEADDR before binding
    class ReuseAddrServer(WSGIServer):
        allow_reuse_address = True

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"Starting server on port {port}...", flush=True)
    server = make_server("0.0.0.0", port, application, server_class=ReuseAddrServer)
    print(f"Server started on http://0.0.0.0:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Server stopped", flush=True)

