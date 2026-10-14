"""api-surface: repo-wide consumed-symbol surfaces and bundling candidates."""

from __future__ import annotations

from pathlib import Path

from package_split.api_surface import analyze_api_surfaces
from package_split.api_surface import bundling_suggestions
from package_split.api_surface import zone_of


ZONES = {
    "shared": "internal-core",
    "shared2": "internal-core",
    "app": "product:app",
    "other": "product:other",
}


def build_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def analyze(tmp_path: Path, roots=("shared", "app", "other"), zones=ZONES):
    source_paths = [tmp_path / r for r in roots]
    return analyze_api_surfaces(tmp_path, source_paths, list(roots), zones)


def test_concentrated_module_suggests_bundling(tmp_path):
    # The user's scenario: a shared module whose symbols are nearly all
    # consumed by a single consumer zone -> bundling candidate.
    build_repo(
        tmp_path,
        {
            "shared/__init__.py": "",
            "shared/formats.py": (
                "def fmt_a():\n    pass\n\n"
                "def fmt_b():\n    pass\n\n"
                "def fmt_c():\n    pass\n\n"
                "def common():\n    pass\n"
            ),
            "app/__init__.py": "",
            "app/consumer.py": ("from shared.formats import fmt_a, fmt_b, fmt_c\nfmt_a()\nfmt_b()\nfmt_c()\n"),
            "other/__init__.py": "",
            "other/consumer.py": "from shared.formats import common\ncommon()\n",
        },
    )
    report = analyze(tmp_path)
    suggestions = {(s.owner, s.consumer_zone): s for s in bundling_suggestions(report)}

    # shared.formats is mostly consumed by product:app -> bundle with app.
    suggestion = suggestions[("shared.formats", "product:app")]
    assert set(suggestion.symbols) == {"fmt_a", "fmt_b", "fmt_c"}
    assert suggestion.single_consumer_file == "app.consumer"

    # common goes the other way: it belongs to product:other's surface.
    assert suggestions[("shared.formats", "product:other")].symbols == ["common"]


def test_broadly_consumed_symbols_are_not_suggested(tmp_path):
    build_repo(
        tmp_path,
        {
            "shared/__init__.py": "",
            "shared/utils.py": "def used_everywhere():\n    pass\n",
            "app/__init__.py": "",
            "app/consumer.py": "from shared.utils import used_everywhere\n",
            "other/__init__.py": "",
            "other/consumer.py": "from shared.utils import used_everywhere\n",
        },
    )
    report = analyze(tmp_path)
    owners = report.owners["shared.utils"]
    assert set(owners.symbols) == {"used_everywhere"}
    assert {s.owner for s in bundling_suggestions(report)} == set()


def test_internal_only_symbols_are_not_external_surface(tmp_path):
    build_repo(
        tmp_path,
        {
            "shared/__init__.py": "",
            "shared/mod.py": "def _private():\n    return 1\n\n\ndef pub():\n    return _private()\n",
            "app/__init__.py": "",
            "app/consumer.py": "from shared.mod import pub\npub()\n",
        },
    )
    report = analyze(tmp_path)
    # _private has no external consumers (used only within shared.mod), so it
    # does not appear in the external surface at all.
    assert set(report.owners["shared.mod"].symbols) == {"pub"}


def test_owner_attribution_uses_longest_module_prefix(tmp_path):
    build_repo(
        tmp_path,
        {
            "shared/__init__.py": "",
            "shared/sub/__init__.py": "",
            "shared/sub/leaf.py": "VALUE = 1\n",
            "shared/other.py": "OTHER = 2\n",
            "app/__init__.py": "",
            "app/consumer.py": (
                "import shared.sub.leaf as leaf\nfrom shared import other\nprint(leaf.VALUE, other.OTHER)\n"
            ),
        },
    )
    report = analyze(tmp_path, roots=("shared", "shared2", "app", "other"))
    assert set(report.owners["shared.sub.leaf"].symbols) == {"VALUE", "<module>"}
    assert set(report.owners["shared.other"].symbols) == {"OTHER", "<module>"}


def test_self_tree_consumers_do_not_block_bundling(tmp_path):
    # A helper consumed by its own package plus exactly one other zone still
    # suggests bundling: the intra-package consumer moves with the package.
    build_repo(
        tmp_path,
        {
            "shared/__init__.py": "",
            "shared/base.py": "def helper():\n    pass\n",
            "shared/sibling.py": "from shared.base import helper\nhelper()\n",
            "app/__init__.py": "",
            "app/consumer.py": "from shared.base import helper\nhelper()\n",
        },
    )
    report = analyze(tmp_path)
    suggestions = {s.owner: s for s in bundling_suggestions(report)}
    suggestion = suggestions["shared.base"]
    assert suggestion.consumer_zone == "product:app"
    assert suggestion.symbols == ["helper"]


def test_same_zone_other_package_consumer_blocks_bundling(tmp_path):
    # A symbol consumed by its own zone from a *different* package (which
    # would not move with the owner) AND another zone is genuine
    # cross-zone surface: no bundling suggestion.
    build_repo(
        tmp_path,
        {
            "shared/__init__.py": "",
            "shared/base.py": "def helper():\n    pass\n",
            "shared2/__init__.py": "",
            "shared2/neighbor.py": "from shared.base import helper\nhelper()\n",
            "app/__init__.py": "",
            "app/consumer.py": "from shared.base import helper\nhelper()\n",
        },
    )
    report = analyze(tmp_path, roots=("shared", "shared2", "app", "other"))
    assert {s.owner for s in bundling_suggestions(report)} == set()


def test_zone_of_longest_prefix_wins():
    zones = {
        "ddtrace.internal": "internal-core",
        "ddtrace.internal.ci_visibility": "product:ci_visibility",
    }
    assert zone_of("ddtrace.internal.ci_visibility.api", zones) == "product:ci_visibility"
    assert zone_of("ddtrace.internal.agent", zones) == "internal-core"
    assert zone_of("ddtrace.contrib.foo", zones) == "foundation"


def test_multi_zone_owner_reports_shared_surface(tmp_path):
    build_repo(
        tmp_path,
        {
            "shared/__init__.py": "",
            "shared/core.py": ("def for_app():\n    pass\n\ndef for_other():\n    pass\n\ndef for_both():\n    pass\n"),
            "app/__init__.py": "",
            "app/consumer.py": "from shared.core import for_app, for_both\n",
            "other/__init__.py": "",
            "other/consumer.py": "from shared.core import for_other, for_both\n",
        },
    )
    report = analyze(tmp_path)
    suggestions = {(s.owner, s.consumer_zone): s for s in bundling_suggestions(report)}
    assert set(suggestions) == {("shared.core", "product:app"), ("shared.core", "product:other")}
    # for_both is consumed by both zones -> stays in the foundation; the
    # zone-specific symbols point at their consumers.
    assert set(suggestions[("shared.core", "product:app")].symbols) == {"for_app"}
    assert set(suggestions[("shared.core", "product:other")].symbols) == {"for_other"}
    surface = report.owners["shared.core"]
    assert surface.consumer_zones("for_both", ZONES) == {"product:app", "product:other"}
