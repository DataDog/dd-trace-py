import asyncio
import hashlib
import subprocess

from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import Response
import urllib3
import uvicorn

from ddtrace import tracer
from ddtrace.appsec._iast._iast_request_context_base import is_iast_request_enabled


def get_app():
    app = FastAPI()

    @app.get("/")
    async def index():
        """Health check endpoint."""
        return {"status": "ok"}

    @app.get("/shutdown")
    async def shutdown():
        tracer.shutdown()
        """Shutdown endpoint for test cleanup."""
        return {"status": "shutting down"}

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "ok"}

    @app.get("/iast-enabled")
    async def iast_enabled(delay_ms: int = 200):
        """Endpoint to test concurrent IAST request enablement.

        Awaits for the given delay and returns whether the IAST request context
        is currently enabled for this request.
        """
        await asyncio.sleep(max(0, delay_ms) / 1000.0)
        return {"enabled": is_iast_request_enabled()}

    @app.get("/iast-header-injection-vulnerability")
    async def header_injection(header: str):
        """Test endpoint for header injection vulnerability."""
        response = Response("OK")
        response.raw_headers.append((b"X-Vulnerable-Header", header.encode()))
        return response

    @app.get("/iast-header-injection-vulnerability-secure")
    async def header_injection_secure(header: str):
        """Test endpoint for secure header handling."""
        response = Response("OK")
        response.headers["X-Vulnerable-Header"] = header
        return response

    @app.get("/iast-cmdi-vulnerability")
    async def cmdi(filename: str):
        """Test endpoint for command injection vulnerability."""
        subp = subprocess.Popen(args=["ls", "-la", filename])
        subp.communicate()
        subp.wait()
        return Response(content="OK")

    @app.get("/iast-cmdi-vulnerability-secure")
    async def cmdi_secure(filename: str):
        """Test endpoint for secure command handling."""
        subp = subprocess.Popen(args=["ls", "-la", filename])
        subp.communicate()
        subp.wait()
        return Response(content="OK")

    @app.post("/iast-cmdi-vulnerability-form")
    async def cmdi_form(command: str = Form(...)):
        """Test endpoint for command injection vulnerability with form data."""
        subp = subprocess.Popen(args=["ls", "-la", command])
        subp.communicate()
        subp.wait()
        return Response(content="OK")

    @app.get("/returnheaders")
    def return_headers(request: Request):
        headers = {}
        for key, value in request.headers.items():
            headers[key] = value
        return JSONResponse(headers)

    @app.get("/vulnerablerequestdownstream")
    def vulnerable_request_downstream(port: int = 8050):
        """Trigger a weak-hash vulnerability, then call downstream return-headers endpoint.

        Mirrors Flask/Django behavior to validate header propagation and IAST instrumentation.
        """
        # Trigger weak hash for IAST
        m = hashlib.md5()
        m.update(b"Nobody inspects")
        m.update(b" the spammish repetition")
        _ = m.digest()
        http_ = urllib3.PoolManager()
        # Sending a GET request and getting back response as HTTPResponse object.
        response = http_.request("GET", f"http://0.0.0.0:{port}/returnheaders")
        return response.data

    return app


app = get_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
