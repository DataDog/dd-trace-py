import asyncio
import hashlib
import json
import subprocess
from urllib.parse import parse_qs

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

    @app.post("/iast-cmdi-vulnerability-form-request")
    async def cmdi_form_request(request: Request):
        """Test endpoint for command injection where form is parsed via request.form().

        This covers the common pattern of accessing form data through the Request object
        instead of declaring parameters with Form in the signature.
        """
        # Exercise Starlette/FastAPI Request.form() code path for IAST tainting
        form = await request.form()
        value = None
        if "command" in form:
            value = form["command"]
        elif form:
            # take the first value if specific key not present
            value = next(iter(form.values()))

        subp = subprocess.Popen(args=["ls", value])
        subp.communicate()
        subp.wait()
        return Response(content="OK")

    @app.post("/iast-cmdi-vulnerability-form-multiple")
    async def cmdi_form_multiple(command: str = Form(...), flag: str = Form("-la")):
        """Test endpoint for command injection with multiple Form parameters.

        Uses multiple form fields declared directly in the function signature. The
        vulnerable value is "command"; an additional field is accepted to mirror
        real-world forms with extra parameters.
        """
        # Additional form parsing variant using multiple Form(...) params
        subp = subprocess.Popen(args=["ls", flag, command])
        subp.communicate()
        subp.wait()
        return Response(content="OK")

    @app.post("/iast-cmdi-vulnerability-body")
    async def cmdi_body(request: Request):
        """Test endpoint for command injection using raw request body across content types.

        This endpoint is intentionally vulnerable and used by tests to validate that IAST correctly
        taints `http.request.body` for different encodings and still reports CMDI on the sink.
        """
        # This endpoint mirrors Django's body-based CMDI test to exercise tainting of request body.
        content_type = request.headers.get("content-type", "")
        raw = await request.body()

        value = None
        text = raw.decode(errors="ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)

        if "application/json" in content_type:
            try:
                data = json.loads(text) if text else None
            except Exception:
                data = None
            if isinstance(data, str):
                value = data
            elif isinstance(data, dict):
                # Prefer common keys used in tests, fallback to first string value
                for key in ("second", "key"):
                    if key in data and isinstance(data[key], str):
                        value = data[key]
                        break
                if value is None:
                    for v in data.values():
                        if isinstance(v, str):
                            value = v
                            break
            elif isinstance(data, list) and data:
                if isinstance(data[0], str):
                    value = data[0]
        elif "application/x-www-form-urlencoded" in content_type:
            form = parse_qs(text)
            # Prefer specific key, otherwise take any first value
            if "master_key" in form and form["master_key"]:
                value = form["master_key"][0]
            elif form:
                value = next(iter(form.values()))[0]
        else:
            # Treat everything else as plain text
            value = text

        # Use the tainted value in a subprocess to trigger the CMDI sink
        subp = subprocess.Popen(args=["ls", value])
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
