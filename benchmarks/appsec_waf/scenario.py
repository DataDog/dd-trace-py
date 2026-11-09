"""One AppSec request through the WAF boundary: context creation, evaluations, result decoding.

Uses the default ruleset, so native matching is included: this measures the share of a WAF call
that the bindings (input conversion, evaluation call, output decoding) contribute. Only APIs present
in every compared wheel are used.
"""

import bm

from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._ddwaf import DDWaf


HEADERS = {
    "host": "example.test",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.5",
    "accept-encoding": "gzip, deflate, br",
    "x-request-id": "0123456789abcdef0123456789abcdef",
    "x-forwarded-for": "192.0.2.1",
    "content-type": "application/json",
}
REQUEST = {
    "server.request.method": "POST",
    "server.request.uri.raw": "/api/v2/products?page=2&sort=price",
    "server.request.headers.no_cookies": HEADERS,
    "server.request.query": {"page": ["2"], "sort": ["price"]},
    "server.request.cookies": {"session": "a3f5c0e1d2b4", "theme": "dark"},
    "server.request.path_params": {"product_id": "12345"},
    "http.client_ip": "192.0.2.1",
}
BODY = {
    "items": [{"id": i, "name": f"product-{i}", "enabled": True, "price": 12.5, "tags": ["a", "b"]} for i in range(24)],
    "metadata": {"cursor": None, "comment": "Livraison prévue à Paris, merci 🙂"},
}
RESPONSE = {
    "server.response.status": "200",
    "server.response.headers.no_cookies": {"content-type": "application/json", "content-length": "2048"},
}

WORKLOADS = {
    # request headers/query, then body, then response: the usual three persistent evaluations
    "request": [REQUEST, {"server.request.body": BODY}, RESPONSE],
    "headers_only": [REQUEST],
    "wide_body": [{"server.request.body": {f"field_{i}": f"value number {i} " * 4 for i in range(200)}}],
    "unicode_body": [
        {"server.request.body": {"城市": "東京" * 50, "emoji": "🦆" * 200, "names": ["café", "élève"] * 50}}
    ],
    "attack": [dict(REQUEST, **{"server.request.query": {"q": ["1' OR '1'='1' --"]}})],
}


class AppSecWaf(bm.Scenario):
    workload: str
    rasp_calls: int

    def run(self):
        with open(DEFAULT.RULES, "rb") as f:
            waf = DDWaf(f.read(), b"", b"")
        steps = WORKLOADS[self.workload]
        statement = {"server.db.statement": "SELECT * FROM products WHERE id = 12345", "server.db.system": "postgresql"}

        def _(loops):
            for _ in range(loops):
                ctx = waf._at_request_start()
                for data in steps:
                    waf.run(ctx, data, timeout_ms=1000)
                for _ in range(self.rasp_calls):
                    waf.run(waf.new_subcontext(ctx), statement, timeout_ms=1000)

        yield _
