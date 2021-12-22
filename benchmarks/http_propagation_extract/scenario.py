import json

import bm

from ddtrace.propagation import http
from ddtrace.propagation import utils


class HTTPPropagationExtract(bm.Scenario):
    headers = bm.var(type=str)
    extra_headers = bm.var(type=int)
    wsgi_style = bm.var(type=bool)

    def generate_headers(self):
        headers = json.loads(self.headers)
        if self.wsgi_style:
            headers = {utils.get_wsgi_header(header): value for header, value in headers.items()}

        for i in range(self.extra_headers):
            header = "x-test-header-{}".format(i)
            if self.wsgi_style:
                header = utils.get_wsgi_header(header)
            headers[header] = str(i)

        return headers

    def run(self):
        headers = self.generate_headers()

        def _(loops):
            for _ in range(loops):
                http.HTTPPropagator.extract(headers)

        yield _
