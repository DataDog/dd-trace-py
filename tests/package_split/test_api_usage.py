"""used-API analysis: detect what other modules consume from a target."""

from __future__ import annotations

from pathlib import Path

from package_split.api_usage import analyze_api_usage
import pytest

from ._support import make_tree


TARGET_FILES = {
    "__init__.py": "",
    "lib.py": (
        "class A:\n"
        "    def method(self):\n"
        "        pass\n"
        "class B(A):\n"
        "    pass\n"
        "def f(x):\n"
        "    return x\n"
        "class E(Exception):\n"
        "    pass\n"
        "CONST = 1\n"
    ),
}


def analyze(tmp_path: Path, package: str, consumers: dict[str, str], target: str = "pkg.lib"):
    for relative, content in consumers.items():
        path = tmp_path / package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return analyze_api_usage(tmp_path, [tmp_path / package], target)


def symbols_of(report) -> dict[str, list[str]]:
    return {
        symbol: sorted({u.kind for u in report.usages if u.symbol == symbol})
        for symbol in {u.symbol for u in report.usages}
    }


def test_attribute_chain_usage_with_alias(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "consumer1.py").write_text(
        "import pkg.lib as L\nL.f(1)\na = L.A()\nvalue = L.CONST\n",
        encoding="utf-8",
    )
    report = analyze(tmp_path, "pkg", {})
    symbols = symbols_of(report)
    assert "f" in symbols and "call" in symbols["f"]
    assert "A" in symbols and "call" in symbols["A"]
    assert "CONST" in symbols and "read" in symbols["CONST"]
    assert all(u.importer == "pkg.consumer1" for u in report.usages)


def test_from_import_usage_kinds(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES)
    consumer = (
        "from pkg.lib import A, f, E as Err, CONST\n"
        "\n"
        "class Sub(A):\n"
        "    pass\n"
        "\n"
        "f(CONST)\n"
        "try:\n"
        "    pass\n"
        "except Err:\n"
        "    raise Err('boom')\n"
    )
    (tmp_path / "pkg" / "consumer2.py").write_text(consumer, encoding="utf-8")
    report = analyze(tmp_path, "pkg", {})
    symbols = symbols_of(report)
    assert "A" in symbols and "subclassed" in symbols["A"]
    assert "f" in symbols and "call" in symbols["f"]
    assert "E" in symbols and "exception-caught" in symbols["E"]
    assert "E" in symbols and "raised" in symbols["E"]
    assert "CONST" in symbols


def test_plain_import_chain_usage(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "consumer3.py").write_text(
        "import pkg.lib\npkg.lib.f(2)\n",
        encoding="utf-8",
    )
    report = analyze(tmp_path, "pkg", {})
    symbols = symbols_of(report)
    assert "call" in symbols["f"]


def test_relative_import_usage(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "consumer4.py").write_text(
        "from .lib import f\nf(3)\n",
        encoding="utf-8",
    )
    report = analyze(tmp_path, "pkg", {})
    symbols = symbols_of(report)
    assert "call" in symbols["f"]


def test_submodule_symbols_are_dotted(tmp_path):
    make_tree(tmp_path, "pkg", {"__init__.py": "", "sub/__init__.py": "", "sub/mod.py": "value = 1\n"})
    (tmp_path / "pkg" / "consumer5.py").write_text(
        "from pkg.sub import mod\nprint(mod.value)\n",
        encoding="utf-8",
    )
    report = analyze(tmp_path, "pkg", {}, target="pkg.sub")
    symbols = symbols_of(report)
    assert "mod.value" in symbols
    assert "mod" in symbols  # the import itself


def test_unused_import_is_still_recorded(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "consumer6.py").write_text(
        "from pkg.lib import f  # imported, never called\n",
        encoding="utf-8",
    )
    report = analyze(tmp_path, "pkg", {})
    symbols = symbols_of(report)
    assert symbols["f"] == ["import"]


def test_star_import_recorded(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "consumer7.py").write_text(
        "from pkg.lib import *\n",
        encoding="utf-8",
    )
    report = analyze(tmp_path, "pkg", {})
    symbols = symbols_of(report)
    assert symbols["*"] == ["import"]


def test_target_self_usage_excluded(tmp_path):
    # The target package's own modules consuming each other do not define
    # the external surface; only outside consumers do.
    make_tree(
        tmp_path,
        "pkg",
        {
            "__init__.py": "",
            "sub/__init__.py": "",
            "sub/mod.py": "value = 1\n",
            "sub/sibling.py": "from pkg.sub.mod import value\nprint(value)\n",
        },
    )
    (tmp_path / "pkg" / "consumer8.py").write_text("from pkg.sub.mod import value\n", encoding="utf-8")
    report = analyze(tmp_path, "pkg", {}, target="pkg.sub")
    symbols = symbols_of(report)
    assert "mod.value" in symbols
    assert all(u.importer == "pkg.consumer8" for u in report.usages)


def test_unrelated_modules_do_not_pollute(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES | {"other.py": "value = 1\n"})
    (tmp_path / "pkg" / "consumer9.py").write_text(
        "from pkg.other import value\nprint(value)\n",
        encoding="utf-8",
    )
    report = analyze(tmp_path, "pkg", {})
    assert report.usages == []


def test_guarded_import_counts_as_usage(tmp_path):
    # try/except ImportError still consumes the symbol.
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "consumer10.py").write_text(
        "try:\n    from pkg.lib import f\nexcept ImportError:\n    f = None\n"
    )
    report = analyze(tmp_path, "pkg", {})
    symbols = symbols_of(report)
    assert "import" in symbols["f"]


def test_aggregation_and_summary(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "c1.py").write_text("from pkg.lib import f\nf(1)\n", encoding="utf-8")
    (tmp_path / "pkg" / "c2.py").write_text("import pkg.lib as L\nL.f(2)\n", encoding="utf-8")
    report = analyze(tmp_path, "pkg", {})
    symbols = report.symbols()
    assert set(symbols["f"]) >= {"import", "call"}
    text = report.summary()
    assert "f" in text
    assert "pkg.c1" in text and "pkg.c2" in text
    assert report.consumer_files == 2


def test_guarded_alias_keeps_every_binding(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES | {"fallback.py": "def f():\n    pass\n"})
    (tmp_path / "pkg" / "consumer10.py").write_text(
        "try:\n    import pkg.lib as impl\nexcept ImportError:\n    import pkg.fallback as impl\nimpl.f()\n",
        encoding="utf-8",
    )
    report = analyze(tmp_path, "pkg", {})
    assert "call" in symbols_of(report)["f"]


def test_function_scope_import_does_not_hide_module_binding(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES | {"other.py": "value = 1\n"})
    (tmp_path / "pkg" / "consumer11.py").write_text(
        "from pkg import lib\n"
        "def g():\n"
        "    from pkg import other as lib\n"
        "    return lib.value\n"
        "def h():\n"
        "    return lib.CONST\n",
        encoding="utf-8",
    )
    report = analyze(tmp_path, "pkg", {})
    assert "read" in symbols_of(report)["CONST"]


def test_unaliased_import_only_is_recorded(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "consumer12.py").write_text("import pkg.lib  # side effects only\n", encoding="utf-8")
    report = analyze(tmp_path, "pkg", {})
    assert symbols_of(report)["<module>"] == ["import"]
    assert report.consumer_files == 1


def test_raise_arguments_are_not_raised(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "consumer13.py").write_text(
        "from pkg.lib import E, f\nraise E(f())\n",
        encoding="utf-8",
    )
    symbols = symbols_of(analyze(tmp_path, "pkg", {}))
    assert "raised" in symbols["E"]
    assert "raised" not in symbols["f"]
    assert "call" in symbols["f"]


def test_unknown_target_is_an_error(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES)
    with pytest.raises(ValueError, match="no scanned module matches pkg.missing"):
        analyze(tmp_path, "pkg", {}, target="pkg.missing")


def test_chain_prefix_is_read_not_call(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "consumer14.py").write_text("import pkg.lib as L\nL.f()\n", encoding="utf-8")
    symbols = symbols_of(analyze(tmp_path, "pkg", {}))
    assert symbols["f"] == ["call"]
    assert symbols["<module>"] == ["import", "read"]


def test_source_encoding_declaration_is_honored(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "consumer15.py").write_bytes(
        "# -*- coding: euc-jp -*-\nfrom pkg.lib import f\nf('日本')\n".encode("euc-jp")
    )
    assert "call" in symbols_of(analyze(tmp_path, "pkg", {}))["f"]


def test_reexport_consumer_is_attributed(tmp_path):
    # Consumers importing through a facade module must land in the used-API
    # surface: `from m import x` where m does `from a.b import x` consumes
    # a.b.x. This is how re-export shims (e.g. ddtrace.testing.internal.
    # tracer_api re-exporting Codeowners) hid real consumers.
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "facade.py").write_text(
        "from pkg.lib import A  # noqa: F401\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "consumer16.py").write_text(
        "from pkg.facade import A\nA()\n",
        encoding="utf-8",
    )
    report = analyze(tmp_path, "pkg", {})
    # The import fact and the call site are both attributed to the consumer.
    consumer_usages = [u for u in report.usages if u.importer == "pkg.consumer16"]
    assert {(u.symbol, u.kind) for u in consumer_usages} == {("A", "import"), ("A", "call")}
    # The facade itself is still a direct consumer.
    assert any(u.importer == "pkg.facade" for u in report.usages)


def test_reexport_chain_resolves_transitively(tmp_path):
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "facade1.py").write_text(
        "from pkg.facade2 import f  # noqa: F401\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "facade2.py").write_text(
        "from pkg.lib import f  # noqa: F401\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "consumer17.py").write_text(
        "from pkg.facade1 import f\nf(1)\n",
        encoding="utf-8",
    )
    symbols = symbols_of(analyze(tmp_path, "pkg", {}))
    assert "call" in symbols["f"]


def test_reexport_cycle_does_not_hang(tmp_path):
    # facade1 -> facade2 -> facade1 is a genuine re-export cycle, but
    # facade2 also binds f directly from the target (the over-approximating
    # multi-binding case), so the origin is still reached and the walk
    # must terminate instead of chasing the cycle forever.
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "facade1.py").write_text(
        "from pkg.facade2 import f  # noqa: F401\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "facade2.py").write_text(
        "from pkg.facade1 import f  # noqa: F401\nfrom pkg.lib import f  # noqa: F401\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "consumer18.py").write_text(
        "from pkg.facade1 import f\nf(1)\n",
        encoding="utf-8",
    )
    symbols = symbols_of(analyze(tmp_path, "pkg", {}))
    assert "call" in symbols["f"]


def test_attribute_chain_through_reexported_module(tmp_path):
    # `import m as r; r.x()` where m re-exports x from the target.
    make_tree(tmp_path, "pkg", TARGET_FILES)
    (tmp_path / "pkg" / "facade.py").write_text(
        "from pkg.lib import A  # noqa: F401\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "consumer19.py").write_text(
        "import pkg.facade as r\nr.A()\n",
        encoding="utf-8",
    )
    symbols = symbols_of(analyze(tmp_path, "pkg", {}))
    assert "call" in symbols["A"]


def test_module_names_follow_workspace_src_layout(tmp_path):
    # uv-workspace destination layout: packages/<dist>/src/<module>/x.py
    # must name as <module>.x, not packages.<dist>.src.<module>.x (which
    # would match no import root and gate nothing).
    from package_split.api_usage import module_names_for

    path = tmp_path / "packages" / "some-dist" / "src" / "pkg" / "mod.py"
    path.parent.mkdir(parents=True)
    path.write_text("value = 1\n", encoding="utf-8")
    assert module_names_for(tmp_path, [tmp_path / "packages" / "some-dist" / "src"]) == {"pkg.mod": path}


def test_module_names_keep_root_src_layout(tmp_path):
    from package_split.api_usage import module_names_for

    path = tmp_path / "src" / "pkg" / "mod.py"
    path.parent.mkdir(parents=True)
    path.write_text("value = 1\n", encoding="utf-8")
    assert module_names_for(tmp_path, [tmp_path / "src"]) == {"pkg.mod": path}
