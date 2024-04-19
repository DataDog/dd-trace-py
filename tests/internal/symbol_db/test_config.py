from ddtrace.settings.symbol_db import SymbolDatabaseConfig


def test_symbol_db_includes_pattern(monkeypatch):
    monkeypatch.setenv("DD_SYMBOL_DATABASE_INCLUDES", "foo,bar")
    c = SymbolDatabaseConfig()

    assert c._includes_re.match("foo")
    assert c._includes_re.match("bar")
    assert c._includes_re.match("foo.baz")

    assert c._includes_re.match("baz") is None
    assert c._includes_re.match("baz.foo") is None
    assert c._includes_re.match("foobar") is None
