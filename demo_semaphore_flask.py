#!/usr/bin/env python
"""
Flask-based demo for Semaphore/BoundedSemaphore lock profiling.

Usage:
  DD_SERVICE=semaphore-flask \
  DD_PROFILING_ENABLED=true \
  DD_TRACE_AGENT_URL=http://localhost:8136 \
  ddtrace-run python demo_semaphore_flask.py

Generate load:
  hey -n 500 -c 50 http://localhost:5000/api/data
"""

from flask import Flask, jsonify
import threading
import time
import random

app = Flask(__name__)

# =============================================================================
# USER CODE - Semaphores defined at module level
# =============================================================================

db_connection_pool = threading.Semaphore(3)
external_api_rate_limiter = threading.BoundedSemaphore(5)
cache_write_semaphore = threading.BoundedSemaphore(2)


def fetch_from_database(query_id: str) -> dict:
    """Simulates fetching data from database using a connection from the pool."""
    with db_connection_pool:
        time.sleep(random.uniform(0.02, 0.08))
        return {"query_id": query_id, "data": "db_result"}


def call_external_api(endpoint: str) -> dict:
    """Simulates calling an external API with rate limiting."""
    with external_api_rate_limiter:
        time.sleep(random.uniform(0.01, 0.05))
        return {"endpoint": endpoint, "response": "api_result"}


def update_cache(key: str, value: str) -> None:
    """Simulates updating a shared cache with write limiting."""
    with cache_write_semaphore:
        time.sleep(random.uniform(0.005, 0.015))


# =============================================================================
# FLASK ROUTES
# =============================================================================

@app.route("/")
def index():
    return jsonify({
        "status": "ok",
        "endpoints": ["/api/data", "/api/db", "/api/external"]
    })


@app.route("/api/data")
def get_data():
    """Main endpoint that exercises all semaphores."""
    request_id = f"req_{random.randint(1000, 9999)}"
    
    db_result = fetch_from_database(request_id)
    api_result = call_external_api(f"/enrich/{request_id}")
    update_cache(request_id, str(db_result))
    
    return jsonify({
        "request_id": request_id,
        "db": db_result,
        "api": api_result,
        "cached": True
    })


@app.route("/api/db")
def get_db_data():
    """Endpoint that only hits the database semaphore."""
    request_id = f"db_{random.randint(1000, 9999)}"
    return jsonify(fetch_from_database(request_id))


@app.route("/api/external")
def get_external_data():
    """Endpoint that only hits the external API bounded semaphore."""
    request_id = f"api_{random.randint(1000, 9999)}"
    return jsonify(call_external_api(f"/data/{request_id}"))


if __name__ == "__main__":
    print("=" * 60)
    print("SEMAPHORE FLASK DEMO")
    print("=" * 60)
    print("Server: http://localhost:5000")
    print("Load test: hey -n 500 -c 50 http://localhost:5000/api/data")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, threaded=True)
