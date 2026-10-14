"""used-API analysis: detect what other modules consume from a target."""

from __future__ import annotations

from pathlib import Path

from package_split.api_usage import analyze_api_usage

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
    return analyze_api_usage(tmp_path, [tmp_path / package], [package], target)


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
