# AppSec WAF HTTP comparison

Compare a baseline tracer checkout with a candidate using the same interpreter:

```sh
scripts/run-benchmarks --waf-http --local-python /path/to/python \
  --baseline /path/to/baseline --candidate /path/to/candidate \
  --requests 1000 --repeats 5 --artifacts artifacts/waf-http
```

Both checkouts must have their native extensions and distribution metadata
installed for that interpreter. The driver starts a fresh server for each sample
and alternates checkout order. `--list`, `--dry-run`, and `--configs` are supported.

The real Flask/Waitress HTTP path includes tracing, parsing, WAF evaluation, and
result handling. A counting sink replaces agent export. IAST, remote config, and
API-security sampling are disabled. A sample is rejected unless AppSec loads
rules, produces completed WAF traces, and returns the expected HTTP statuses.

`results/macos-arm64.json` records five repetitions per checkout and workload
with full desktop native features. See
[integration notes](../../docs/appsec-native-waf.md) for scope and qualification.
