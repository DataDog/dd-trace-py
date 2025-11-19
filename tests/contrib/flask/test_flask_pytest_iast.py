#!/usr/bin/env python3
"""
This test suite is actually used as fixture in end-to-end test
for pytest IAST integration.
"""

import urllib.parse

import pytest

from .app import app as real_app


@pytest.fixture()
def app():
    return real_app


@pytest.fixture()
def client(app):
    return app.test_client()


def test_md5_request(client):
    data = b"foobar"
    urlencoded_data = urllib.parse.urlencode({"q": data})
    response = client.get("/md5sum?%s" % urlencoded_data)
    assert response.status_code == 200
