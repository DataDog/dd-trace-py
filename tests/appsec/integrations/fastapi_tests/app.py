from fastapi import FastAPI, Form
from fastapi.responses import Response
from ddtrace import tracer
import uvicorn
import subprocess


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

    return app


app = get_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
