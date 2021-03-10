import os
import subprocess
import sys

from ddtrace.vendor import six
from tests import snapshot


code = """
import datetime
import os

import %s as elasticsearch

ELASTICSEARCH_CONFIG = {"port": int(os.getenv("TEST_ELASTICSEARCH_PORT", 9200)),}
ES_INDEX = "ddtrace_index"
ES_TYPE = "ddtrace_type"
mapping = {"mapping": {"properties": {"created": {"type": "date", "format": "yyyy-MM-dd"}}}}
es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG["port"])
es.indices.create(index=ES_INDEX, ignore=400, body=mapping)

args = {"index": ES_INDEX, "doc_type": ES_TYPE}
es.indices.delete(index=ES_INDEX, ignore=[400, 404])
"""


@snapshot(async_mode=False)
def test_elasticsearch(tmpdir):
    f = tmpdir.join("test.py")
    f.write(code % "elasticsearch")
    p = subprocess.Popen(
        ["ddtrace-run", sys.executable, "test.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmpdir),
        env=os.environ,
    )
    p.wait()
    stderr = p.stderr.read()
    stdout = p.stdout.read()
    assert stderr == six.b(""), stderr
    assert stdout == six.b(""), stdout
    assert p.returncode == 0


@snapshot(async_mode=False)
def test_elasticsearch2(tmpdir):
    f = tmpdir.join("test.py")
    f.write(code % "elasticsearch2")
    p = subprocess.Popen(
        ["ddtrace-run", sys.executable, "test.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmpdir),
        env=os.environ,
    )
    p.wait()
    stderr = p.stderr.read()
    stdout = p.stdout.read()
    assert stderr == six.b(""), stderr
    assert stdout == six.b(""), stdout
    assert p.returncode == 0


@snapshot(async_mode=False)
def test_elasticsearch5(tmpdir):
    f = tmpdir.join("test.py")
    f.write(code % "elasticsearch5")
    p = subprocess.Popen(
        ["ddtrace-run", sys.executable, "test.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmpdir),
        env=os.environ,
    )
    p.wait()
    stderr = p.stderr.read()
    stdout = p.stdout.read()
    assert stderr == six.b(""), stderr
    assert stdout == six.b(""), stdout
    assert p.returncode == 0


@snapshot(async_mode=False)
def test_elasticsearch6(tmpdir):
    f = tmpdir.join("test.py")
    f.write(code % "elasticsearch6")
    p = subprocess.Popen(
        ["ddtrace-run", sys.executable, "test.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmpdir),
        env=os.environ,
    )
    p.wait()
    stderr = p.stderr.read()
    stdout = p.stdout.read()
    assert stderr == six.b(""), stderr
    assert stdout == six.b(""), stdout
    assert p.returncode == 0


@snapshot(async_mode=False)
def test_elasticsearch7(tmpdir):
    f = tmpdir.join("test.py")
    f.write(code % "elasticsearch7")
    p = subprocess.Popen(
        ["ddtrace-run", sys.executable, "test.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmpdir),
        env=os.environ,
    )
    p.wait()
    stderr = p.stderr.read()
    stdout = p.stdout.read()
    assert stderr == six.b(""), stderr
    assert stdout == six.b(""), stdout
    assert p.returncode == 0
