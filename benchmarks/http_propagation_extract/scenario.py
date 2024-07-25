import json

import bm

from ddtrace import config
from ddtrace.propagation import _utils as utils
from ddtrace.propagation import http


class HTTPPropagationExtract(bm.Scenario):
    headers: str
    extra_headers: int
    wsgi_style: bool
    styles: str

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
        if self.styles:
            config._propagation_style_extract = self.styles.split(",") if ("," in self.styles) else [self.styles]

        headers = self.generate_headers()

        def _(loops):
            for _ in range(loops):
                http.HTTPPropagator.extract(headers)

        yield _
