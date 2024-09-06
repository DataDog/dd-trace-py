import os
import subprocess
import sys

from tests.utils import snapshot


SNAPSHOT_IGNORES = ["meta.server.address", "meta.out.host"]

code = """
import datetime
import os

import %s as elasticsearch

ELASTICSEARCH_CONFIG = {
  "host": os.getenv("TEST_ELASTICSEARCH_HOST", "127.0.0.1"),
  "port": int(os.getenv("TEST_ELASTICSEARCH_PORT", 9200)),
}
ES_INDEX = "ddtrace_index"
ES_URL = "http://%%s:%%d" %% (ELASTICSEARCH_CONFIG["host"], ELASTICSEARCH_CONFIG["port"])
es = elasticsearch.Elasticsearch(hosts=[ES_URL])
if elasticsearch.__version__ >= (8, 0, 0):
    es.options(ignore_status=400).indices.create(index=ES_INDEX)
    es.options(ignore_status=[400, 404]).indices.delete(index=ES_INDEX)
else:
    es.indices.create(index=ES_INDEX, ignore=400)
    es.indices.delete(index=ES_INDEX, ignore=[400, 404])
"""


def do_test(tmpdir, es_version):
    f = tmpdir.join("test.py")
    f.write(code % es_version)
    env = os.environ.copy()
    # ddtrace-run patches sqlite3 which is used by coverage to store coverage
    # results. This generates sqlite3 spans during the test run which interfere
    # with the snapshot. So disable sqlite3.
    env.update({"DD_TRACE_SQLITE3_ENABLED": "false"})
    p = subprocess.Popen(
        ["ddtrace-run", sys.executable, "test.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmpdir),
        env=env,
    )
    p.wait()
    stderr = p.stderr.read()
    stdout = p.stdout.read()
    assert stderr == b"", stderr
    assert stdout == b"", stdout
    assert p.returncode == 0


@snapshot(async_mode=False, ignores=SNAPSHOT_IGNORES)
def test_elasticsearch(tmpdir):
    do_test(tmpdir, "elasticsearch")


@snapshot(async_mode=False, ignores=SNAPSHOT_IGNORES)
def test_elasticsearch7(tmpdir):
    do_test(tmpdir, "elasticsearch7")
