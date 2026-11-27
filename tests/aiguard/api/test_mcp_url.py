"""mcp_servers entries: request-local server IDs, and server URLs that follow the WHATWG URL Standard."""

import pytest

from ddtrace.aiguard.integrations._mcp import mcp_reference
from ddtrace.aiguard.integrations._mcp import mcp_server
from ddtrace.aiguard.integrations._mcp import mcp_url
from ddtrace.aiguard.integrations._mcp import referenced_mcp_servers
from ddtrace.aiguard.integrations._mcp import safe_stdio_args


@pytest.mark.parametrize(
    "url,expected",
    [
        # Scheme, host, port and path; no userinfo, query or fragment, which can hold credentials.
        ("https://mcp.example.com/mcp", "https://mcp.example.com/mcp"),
        ("HTTPS://MCP.Example.COM:443/Mcp/", "https://mcp.example.com/Mcp/"),
        ("http://mcp.example.com:80/sse", "http://mcp.example.com/sse"),
        ("http://mcp.example.com:8080/sse", "http://mcp.example.com:8080/sse"),
        ("https://user:secret@mcp.example.com/mcp?token=abc#frag", "https://mcp.example.com/mcp"),
        ("https://mcp.example.com?token=abc", "https://mcp.example.com/"),
        ("ws://h:80/x", "ws://h/x"),
        ("wss://h:443/x", "wss://h/x"),
        ("http://my_server.example/mcp", "http://my_server.example/mcp"),
        ("http://example.com./mcp", "http://example.com./mcp"),
        # The path: "/" when empty, dot segments resolved, backslashes as slashes, percent-encoded.
        ("https://mcp.example.com", "https://mcp.example.com/"),
        ("https://mcp.example.com/a/./b/../mcp", "https://mcp.example.com/a/mcp"),
        ("https://mcp.example.com/a/..", "https://mcp.example.com/"),
        ("https://mcp.example.com/a/.", "https://mcp.example.com/a/"),
        ("https://mcp.example.com/../../mcp", "https://mcp.example.com/mcp"),
        ("https://mcp.example.com/%2E%2e/x/%2e/mcp", "https://mcp.example.com/x/mcp"),
        ("https://mcp.example.com/a\\b", "https://mcp.example.com/a/b"),
        ("https://mcp.example.com/a b/\u00fc/{x}/^", "https://mcp.example.com/a%20b/%C3%BC/%7Bx%7D/%5E"),
        ("https://mcp.example.com/a%2Fb//c", "https://mcp.example.com/a%2Fb//c"),
        # Leading and trailing C0 controls and spaces are stripped, and tabs and newlines removed.
        (" \x00https://mcp.example.com/m\tc\np \n", "https://mcp.example.com/mcp"),
        # WHATWG ends the authority at a backslash and HTTP clients such as httpx do not: such a URL has no url.
        ("https://a\\@b/mcp", None),
        ("https://CORP\\alice:s3cret@mcp.internal/mcp", None),
        ("ws://a\\b:1@c/x", None),
        # IPv4 literals in any number form become the dotted quad.
        ("http://2130706433/mcp", "http://127.0.0.1/mcp"),
        ("http://0x7f.1/mcp", "http://127.0.0.1/mcp"),
        ("http://0X7F000001/mcp", "http://127.0.0.1/mcp"),
        ("http://127.1/mcp", "http://127.0.0.1/mcp"),
        ("http://0177.0.0.1/mcp", "http://127.0.0.1/mcp"),
        ("http://127.0.0.1./mcp", "http://127.0.0.1/mcp"),
        ("http://1.2.3.04/mcp", "http://1.2.3.4/mcp"),
        ("http://1.2.3/mcp", "http://1.2.0.3/mcp"),
        ("http://0x/mcp", "http://0.0.0.0/mcp"),
        ("http://127.0.0.1:8080/mcp", "http://127.0.0.1:8080/mcp"),
        # Invalid IPv4 literals have no url.
        ("http://4294967296/mcp", None),
        ("http://256.1.1.1/mcp", None),
        ("http://1.2.3.4.5/mcp", None),
        ("http://00x7f.1/mcp", None),
        # IPv6 literals get the WHATWG serialization.
        ("http://[::1]:9000/mcp", "http://[::1]:9000/mcp"),
        ("http://[0:0::1]:9000/mcp", "http://[::1]:9000/mcp"),
        ("http://[::ffff:1.2.3.4]/mcp", "http://[::ffff:102:304]/mcp"),
        ("http://[2001:DB8:0:0:1:0:0:1]/mcp", "http://[2001:db8::1:0:0:1]/mcp"),
        ("http://[1:0:0:2:0:0:0:3]/mcp", "http://[1:0:0:2::3]/mcp"),
        ("http://[fe80::1%25eth0]/mcp", None),
        # The host is percent-decoded, and hosts that map to ASCII get the mapped host.
        ("http://%65xample.com/mcp", "http://example.com/mcp"),
        ("http://ｅｘａｍｐｌｅ.com/mcp", "http://example.com/mcp"),
        ("http://example。com/mcp", "http://example.com/mcp"),
        ("http://ex\u00adample.com/mcp", "http://example.com/mcp"),
        # IDN hosts, and hosts that the WHATWG parser rejects, have no url.
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
        # Other compatibility characters are not mapped: the host gets no url instead of a wrong one.
        ("http://\u2121.com/mcp", None),
        # IPv4 parts accept only the digits of their base: no sign and no underscore.
        ("http://1_2.0.0.1/mcp", None),
        ("http://-1.1/mcp", None),
        ("http://+1.0.0.1/mcp", None),
        ("http://0x1_f.1/mcp", None),
        ("http://\uff11\uff12\uff17.0.0.1/mcp", "http://127.0.0.1/mcp"),
        # Other schemes keep their lowercase host and their path as written.
        ("custom://Host:80/x/y?q=1", "custom://host:80/x/y"),
        ("custom://a%25.HOST.example/x", "custom://a%25.host.example/x"),
        ("ftp://H:21/a/../b", "ftp://h:21/a/../b"),
        # Whatever the scheme, an IDN host has no url.
        ("ftp://b\u00fccher.example/x", None),
        ("custom://xn--bcher-kva.example/x", None),
        ("custom://a%25.XN--bcher-kva.example/x", None),
        # Without a scheme or a host there is no url.
        ("not a url", None),
        ("", None),
    ],
)
def test_mcp_url(url, expected):
    assert mcp_url(url) == expected
    if expected is not None:
        assert mcp_url(expected) == expected


def test_mcp_server_ids_only_join_tool_calls_to_their_request_entry():
    """The ID is a position in the request, not a fingerprint: the same server gets the ID it is given."""
    first = mcp_server("provider", "unknown", name="docs", url="https://mcp.example.com/mcp")
    second = mcp_server("provider", "unknown", name="docs", url="https://mcp.example.com/mcp", server_id="mcp_2")
    assert first == {
        "id": "mcp_1",
        "managed_by": "provider",
        "transport": "unknown",
        "name": "docs",
        "url": "https://mcp.example.com/mcp",
        "tools": {},
    }
    assert second == dict(first, id="mcp_2")


def test_mcp_server_without_url_for_idn_host_keeps_its_name():
    server = mcp_server("client", "streamable_http", name="atlassian", url="http://bücher.example/mcp")
    assert server == {
        "id": "mcp_1",
        "managed_by": "client",
        "transport": "streamable_http",
        "name": "atlassian",
        "tools": {},
    }
    # Nothing tells the server apart: no entry, so its tool calls stay ordinary.
    assert mcp_server("client", "streamable_http", url="http://bücher.example/mcp") is None


def test_mcp_server_for_idna_deviation_has_no_url():
    """fa\u00df.de and fass.de are different domains, so fa\u00df.de must not be sent as fass.de."""
    server = mcp_server("client", "streamable_http", name="atlassian", url="https://fa\u00df.de/mcp")
    assert "url" not in server


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
def test_stdio_command_is_reported_as_its_program_name(command, program):
    """A full path can hold the user name and changes per machine: only the program name is kept."""
    assert mcp_server("client", "stdio", command=command)["command"] == program


def test_stdio_command_path():
    server = mcp_server(
        "client", "stdio", command="/Users/alice/.nvm/versions/node/v22.1.0/bin/npx", args=["@acme/docs"]
    )
    assert server == {
        "id": "mcp_1",
        "managed_by": "client",
        "transport": "stdio",
        "command": "npx",
        "args": ["@acme/docs"],
        "tools": {},
    }


def test_stdio_command_without_program_name():
    assert mcp_server("client", "stdio", command="/usr/bin/") is None
    assert mcp_server("client", "stdio", command="/usr/bin/", name="atlassian") == {
        "id": "mcp_1",
        "managed_by": "client",
        "transport": "stdio",
        "name": "atlassian",
        "tools": {},
    }


def test_stdio_drive_relative_command_keeps_its_drive_prefix():
    """No drive-letter handling, so c:npx is not taken for npx and no argument is sent."""
    server = mcp_server("client", "stdio", command="C:npx", args=safe_stdio_args("C:npx", ["-y", "private-value"]))
    assert server == {"id": "mcp_1", "managed_by": "client", "transport": "stdio", "command": "c:npx", "tools": {}}


def test_referenced_mcp_servers_keep_only_the_called_servers_and_tools():
    docs = mcp_server("provider", "unknown", name="docs")
    wiki = mcp_server("provider", "unknown", name="wiki", server_id="mcp_2")
    docs_call = {
        "id": "c1",
        "function": {"name": "search", "arguments": "{}"},
        "mcp": mcp_reference(docs, "search", "search"),
    }
    wiki["tools"]["unused"] = "unused"
    wiki_call = {
        "id": "c2",
        "function": {"name": "read", "arguments": "{}"},
        "mcp": mcp_reference(wiki, "read", "read"),
    }
    ordinary = {"id": "c3", "function": {"name": "read", "arguments": "{}"}}
    unknown = {"id": "c4", "function": {"name": "read", "arguments": "{}"}, "mcp": {"server_id": "mcp_9"}}
    messages = [{"role": "assistant", "tool_calls": [wiki_call, ordinary, unknown]}]

    assert referenced_mcp_servers(messages, [docs, wiki]) == [dict(wiki, tools={"read": "read"})]
    assert referenced_mcp_servers([{"role": "assistant", "tool_calls": [docs_call]}], [docs, wiki]) == [docs]
    assert referenced_mcp_servers([{"role": "user", "content": "hi"}], [docs, wiki]) is None
