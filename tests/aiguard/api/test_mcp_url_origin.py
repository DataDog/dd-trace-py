"""The URL origin of MCP server IDs: the host follows the WHATWG URL Standard.

Every tracer canonicalizes IP literals the same way and leaves out IDN hosts, so that the tracers
compute the same server ID. The other tracers test the same vectors.
"""

import pytest

from ddtrace.aiguard.integrations._mcp import mcp_server
from ddtrace.aiguard.integrations._mcp import mcp_server_id
from ddtrace.aiguard.integrations._mcp import mcp_url_origin
from ddtrace.aiguard.integrations._mcp import safe_stdio_args


@pytest.mark.parametrize(
    "url,expected",
    [
        # Unchanged behavior: origin only, lowercase, no default port, no secrets.
        ("https://mcp.example.com/mcp", "https://mcp.example.com"),
        ("HTTPS://MCP.Example.COM:443/Mcp/", "https://mcp.example.com"),
        ("http://mcp.example.com:80/sse", "http://mcp.example.com"),
        ("http://mcp.example.com:8080/sse", "http://mcp.example.com:8080"),
        ("https://user:secret@mcp.example.com/mcp?token=abc#frag", "https://mcp.example.com"),
        ("ws://h:80/x", "ws://h"),
        ("wss://h:443/x", "wss://h"),
        ("http://my_server.example/mcp", "http://my_server.example"),
        ("http://example.com./mcp", "http://example.com."),
        # IPv4 literals in any number form become the dotted quad.
        ("http://2130706433/mcp", "http://127.0.0.1"),
        ("http://0x7f.1/mcp", "http://127.0.0.1"),
        ("http://0X7F000001/mcp", "http://127.0.0.1"),
        ("http://127.1/mcp", "http://127.0.0.1"),
        ("http://0177.0.0.1/mcp", "http://127.0.0.1"),
        ("http://127.0.0.1./mcp", "http://127.0.0.1"),
        ("http://1.2.3.04/mcp", "http://1.2.3.4"),
        ("http://1.2.3/mcp", "http://1.2.0.3"),
        ("http://0x/mcp", "http://0.0.0.0"),
        ("http://127.0.0.1:8080/mcp", "http://127.0.0.1:8080"),
        # Invalid IPv4 literals have no origin.
        ("http://4294967296/mcp", None),
        ("http://256.1.1.1/mcp", None),
        ("http://1.2.3.4.5/mcp", None),
        ("http://00x7f.1/mcp", None),
        # IPv6 literals get the WHATWG serialization.
        ("http://[::1]:9000/mcp", "http://[::1]:9000"),
        ("http://[0:0::1]:9000/mcp", "http://[::1]:9000"),
        ("http://[::ffff:1.2.3.4]/mcp", "http://[::ffff:102:304]"),
        ("http://[2001:DB8:0:0:1:0:0:1]/mcp", "http://[2001:db8::1:0:0:1]"),
        ("http://[1:0:0:2:0:0:0:3]/mcp", "http://[1:0:0:2::3]"),
        ("http://[fe80::1%25eth0]/mcp", None),
        # The host is percent-decoded, and hosts that map to ASCII get the mapped host.
        ("http://%65xample.com/mcp", "http://example.com"),
        ("http://ｅｘａｍｐｌｅ.com/mcp", "http://example.com"),
        ("http://example。com/mcp", "http://example.com"),
        ("http://ex­ample.com/mcp", "http://example.com"),
        # IDN hosts, and hosts that the WHATWG parser rejects, have no origin.
        ("http://bücher.example/mcp", None),
        ("http://Bücher.example/mcp", None),
        ("http://xn--bcher-kva.example/mcp", None),
        ("http://MCP.XN--BCHER-KVA.example/mcp", None),
        ("http://b%C3%BCcher.example/mcp", None),
        ("http://exa%20mple.com/mcp", None),
        # IDNA deviations (\u00df, \u03c2, joiners): WHATWG keeps or rejects them, so they never map to ASCII.
        ("http://fa\u00df.de/mcp", None),
        ("http://FA\u1e9e.de/mcp", None),
        ("http://\u03c3\u03c2.de/mcp", None),
        ("http://ex\u200dample.com/mcp", None),
        ("http://ex\u200cample.com/mcp", None),
        # Other compatibility characters are not mapped: the host gets no url key instead of a wrong one.
        ("http://\u2121.com/mcp", None),
        # IPv4 parts accept only the digits of their base: no sign and no underscore.
        ("http://1_2.0.0.1/mcp", None),
        ("http://-1.1/mcp", None),
        ("http://+1.0.0.1/mcp", None),
        ("http://0x1_f.1/mcp", None),
        ("http://\uff11\uff12\uff17.0.0.1/mcp", "http://127.0.0.1"),
        # Other schemes keep the url_origin rules.
        ("custom://Host:80/x", "custom://host:80"),
        ("not a url", None),
        ("", None),
    ],
)
def test_mcp_url_origin(url, expected):
    assert mcp_url_origin(url) == expected
    if expected is not None:
        assert mcp_url_origin(expected) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://2130706433/mcp", "mcp_bb3842ff8c77d25d"),
        ("http://127.0.0.1/mcp", "mcp_bb3842ff8c77d25d"),
        ("http://127.0.0.1:8080/mcp", "mcp_b339e608ea2ddcc1"),
        ("http://[0:0::1]:9000/mcp", "mcp_cc35033de8004f7e"),
        ("http://%65xample.com/mcp", "mcp_35da95b0ebe35a00"),
        ("http://my_server.example/mcp", "mcp_2607363cabc2eebc"),
    ],
)
def test_mcp_server_id_cross_tracer_vectors(url, expected):
    assert mcp_server_id(url=url) == expected


def test_mcp_server_without_url_for_idn_host_uses_next_key():
    server = mcp_server("client", "streamable_http", name="atlassian", url="http://bücher.example/mcp")
    assert server is not None
    assert "url" not in server
    assert server["id"] == "mcp_4b1df2a2d821278c"
    assert mcp_server("client", "streamable_http", url="http://bücher.example/mcp") is None


def test_mcp_server_for_idna_deviation_uses_next_key():
    """fa\u00df.de and fass.de are different domains, so fa\u00df.de must not get the url key of fass.de."""
    server = mcp_server("client", "streamable_http", name="atlassian", url="https://fa\u00df.de/mcp")
    assert server is not None
    assert "url" not in server
    assert server["id"] == "mcp_4b1df2a2d821278c"
    assert server["id"] != mcp_server_id(url="https://fass.de/mcp")


@pytest.mark.parametrize(
    "command,program",
    [
        ("/Users/alice/.nvm/versions/node/v22.1.0/bin/npx", "npx"),
        ("C:\\Users\\alice\\AppData\\Roaming\\npm\\npx.cmd", "npx"),
        ("C:/Program Files/nodejs/NPX.CMD", "npx"),
        ("/usr/bin/Python3.12", "python3.12"),
        ("uvx.exe", "uvx"),
        ("node", "node"),
    ],
)
def test_stdio_command_is_reported_and_hashed_as_its_program_name(command, program):
    """A full path can hold the user name and changes per machine: only the program name is kept."""
    server = mcp_server("client", "stdio", command=command)
    assert server["command"] == program
    assert server["id"] == mcp_server_id(command=program)


def test_stdio_command_path_vector():
    """Cross-tracer vector: the other tracers test the same command and ID."""
    server = mcp_server(
        "client", "stdio", command="/Users/alice/.nvm/versions/node/v22.1.0/bin/npx", args=["@acme/docs"]
    )
    assert server["id"] == "mcp_ebcb9861e648b910"
    assert "alice" not in str(server)


def test_stdio_command_without_program_name_uses_the_next_key():
    assert mcp_server("client", "stdio", command="/usr/bin/") is None
    assert mcp_server("client", "stdio", command="/usr/bin/", name="atlassian")["id"] == mcp_server_id(name="atlassian")


def test_stdio_drive_relative_command_keeps_its_drive_prefix():
    """Cross-tracer vector: no drive-letter handling, so c:npx is not taken for npx and no argument is sent."""
    server = mcp_server("client", "stdio", command="C:npx", args=safe_stdio_args("C:npx", ["-y", "private-value"]))
    assert server == {
        "id": "mcp_728353ed9e10df60",
        "managed_by": "client",
        "transport": "stdio",
        "command": "c:npx",
        "tools": {},
    }
