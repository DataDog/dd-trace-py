from ddtrace.internal import agent


def test_hostname(monkeypatch):
    assert agent.get_hostname() == "localhost"
    monkeypatch.setenv("DD_AGENT_HOST", "host")
    assert agent.get_hostname() == "host"


def test_trace_port(monkeypatch):
    assert agent.get_trace_port() == 8126
    monkeypatch.setenv("DD_TRACE_AGENT_PORT", "1235")
    assert agent.get_trace_port() == 1235
    monkeypatch.setenv("DD_AGENT_PORT", "1234")
    assert agent.get_trace_port() == 1234


def test_stats_port(monkeypatch):
    assert agent.get_stats_port() == 8125
    monkeypatch.setenv("DD_DOGSTATSD_PORT", "1235")
    assert agent.get_stats_port() == 1235


def test_trace_url(monkeypatch):
    assert agent.get_trace_url() == "http://localhost:8126"
    monkeypatch.setenv("DD_TRACE_AGENT_PORT", "1235")
    assert agent.get_trace_url() == "http://localhost:1235"
    monkeypatch.setenv("DD_AGENT_HOST", "mars")
    assert agent.get_trace_url() == "http://mars:1235"

    monkeypatch.setenv("DD_TRACE_AGENT_URL", "http://saturn:1111")
    assert agent.get_trace_url() == "http://saturn:1111"


def test_stats_url(monkeypatch):
    assert agent.get_stats_url() == "udp://localhost:8125"
    monkeypatch.setenv("DD_AGENT_HOST", "saturn")
    assert agent.get_stats_url() == "udp://saturn:8125"
    monkeypatch.setenv("DD_DOGSTATSD_PORT", "1235")
    assert agent.get_stats_url() == "udp://saturn:1235"

    monkeypatch.setenv("DD_DOGSTATSD_URL", "udp://mars:1234")
    assert agent.get_stats_url() == "udp://mars:1234"
