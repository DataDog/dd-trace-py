"""Alternate fresh checkout processes and retain all HTTP macrobenchmark samples."""

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import selectors
import statistics
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
SERVER = Path(__file__).with_name("server.py")


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def sample(checkout, workload, count):
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(checkout),
            "DD_APPSEC_ENABLED": "true",
            "DD_API_SECURITY_ENABLED": "false",
            "DD_IAST_ENABLED": "false",
            "DD_REMOTE_CONFIGURATION_ENABLED": "false",
            "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
            "DD_TRACE_SAMPLE_RATE": "1",
            "DD_TRACE_STARTUP_LOGS": "false",
            "DD_TRACE_ENABLED": "true",
            "DD_APPSEC_WAF_TIMEOUT": "10",
            "DD_TRACE_RESOURCE_RENAMING_ENABLED": "false",
        }
    )
    with subprocess.Popen(
        [sys.executable, str(SERVER)],
        cwd=checkout,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as server:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout, selectors.EVENT_READ)
                if not selector.select(timeout=30):
                    raise RuntimeError("benchmark server did not start within 30 seconds")
            ready = server.stdout.readline()
            if not ready:
                raise RuntimeError(server.stderr.read())
            port = json.loads(ready)["port"]
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            headers = {"User-Agent": "Mozilla/5.0"}
            method, body, expected = "GET", None, 200
            if workload in ("body", "unicode"):
                text = "élève東京🙂" * 12 if workload == "unicode" else "ordinary customer message" * 4
                body = json.dumps(
                    {"rows": [{"id": i, "name": "alice", "message": text} for i in range(200)]}, ensure_ascii=False
                ).encode()
                method = "POST"
                headers["Content-Type"] = "application/json"
            elif workload == "blocked":
                headers["User-Agent"] = "dd-test-scanner-log-block"
                expected = 403

            def request():
                connection.request(method, "/", body=body, headers=headers)
                response = connection.getresponse()
                response.read()
                if response.status != expected:
                    raise AssertionError((str(checkout), workload, response.status, expected))

            def snapshot():
                connection.request("GET", "/__stats")
                response = connection.getresponse()
                payload = response.read()
                if response.status != 200:
                    raise RuntimeError(("stats endpoint failed", response.status, payload))
                return json.loads(payload)

            for _ in range(30):
                request()
            before = snapshot()
            latencies = []
            start = time.perf_counter()
            for _ in range(count):
                tick = time.perf_counter_ns()
                request()
                latencies.append(time.perf_counter_ns() - tick)
            elapsed = time.perf_counter() - start
            after = snapshot()
            if after["waf_traces"] - before["waf_traces"] != count:
                raise AssertionError(("WAF was not exercised for every request", before, after))
            if Path(after["checkout"]).resolve() != checkout:
                raise AssertionError(("wrong checkout", str(checkout), after))
            connection.close()
            return {
                "requests": count,
                "wall_seconds": elapsed,
                "requests_per_second": count / elapsed,
                "server_cpu_us_per_request": (after["cpu_seconds"] - before["cpu_seconds"]) * 1e6 / count,
                "latency_p50_us": percentile(latencies, 0.50) / 1000,
                "latency_p95_us": percentile(latencies, 0.95) / 1000,
                "rss_bytes": after["rss_bytes"],
                "evidence": after,
                "timeouts": after["timeouts"] - before["timeouts"],
                "events": after["events"] - before["events"],
            }
        except Exception:
            server.terminate()
            server.wait(timeout=10)
            print(server.stderr.read(), file=sys.stderr)
            raise
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", default=ROOT, type=Path)
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--workloads", default="get,body,unicode,blocked")
    args = parser.parse_args()
    if args.requests < 1 or args.repeats < 1:
        parser.error("requests and repeats must be positive")
    checkouts = {"baseline": args.baseline.resolve(), "candidate": args.candidate.resolve()}
    for path in checkouts.values():
        if not (path / "ddtrace" / "__init__.py").is_file():
            parser.error(f"checkout must contain an installed ddtrace source tree: {path}")
    workloads = args.workloads.split(",")
    if set(workloads) - {"get", "body", "unicode", "blocked"}:
        parser.error("unknown workload")
    samples = {name: {backend: [] for backend in ("baseline", "candidate")} for name in workloads}
    for name in workloads:
        for repeat in range(args.repeats):
            for backend in ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline"):
                result = sample(checkouts[backend], name, args.requests)
                samples[name][backend].append(result)
                print(f"{name} {backend}: {result['server_cpu_us_per_request']:.1f} CPU us/request", flush=True)
    medians = {
        name: {
            backend: {
                key: statistics.median(item[key] for item in items)
                for key in (
                    "server_cpu_us_per_request",
                    "latency_p50_us",
                    "latency_p95_us",
                    "requests_per_second",
                    "rss_bytes",
                )
            }
            for backend, items in values.items()
        }
        for name, values in samples.items()
    }
    report = {
        "checkouts": {name: str(path) for name, path in checkouts.items()},
        "python": sys.version,
        "platform": platform.platform(),
        "samples": samples,
        "medians": medians,
        "native_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT / "ddtrace/internal/native").glob("_native*.so")
            if "darwin" in p.name or platform.system() != "Darwin"
        },
        "scope": (
            "single Waitress worker and Flask application; HTTP keepalive; separate loopback HTTP client; "
            "real tracer and bundled rules; "
            "export replaced with counting sink; baseline and candidate checkouts; warm requests"
        ),
    }
    directory = Path(args.artifacts)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(medians, indent=2))


if __name__ == "__main__":
    main()
