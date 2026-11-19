"""Synthetic shop sources shared by local and staging TIA scenarios."""

from pathlib import Path
import textwrap


def write_corpus(root: Path) -> None:
    sources = {
        ".gitignore": "__pycache__/\n.pytest_cache/\n.artifacts/\n.coverage*\n",
        "pytest.ini": "[pytest]\ntestpaths = tests\n",
        "shop/__init__.py": "NAME = 'shop'\n",
        "shop/cart.py": "def items(quantity): return list(range(quantity))\n",
        "shop/shipping.py": "def fee(): return 5\n",
        "shop/stock.py": "def available(): return True\n",
        "shop/currency.py": "UNIT = 'USD'\n",
        "shop/child.py": "def value(): return 42\n",
        "shop/settings.py": "VALUE = 1\n",
        "conftest.py": "pytest_plugins = ['shop.fixtures']\n",
        "shop/fixtures.py": """
            import pytest
            @pytest.fixture(scope="session")
            def currency():
                from shop.currency import UNIT
                return UNIT
        """,
        "tests/test_cart.py": """
            import pytest
            from shop.cart import items
            @pytest.mark.parametrize("quantity", [1, 2])
            def test_add(quantity, currency):
                assert currency == "USD"
                assert len(items(quantity)) == quantity
        """,
        "tests/test_shipping.py": """
            from shop.shipping import fee
            def test_fee(currency):
                assert currency == "USD"
                assert fee() == 5
        """,
        "tests/test_threads.py": """
            import threading
            from shop.stock import available
            def test_stock():
                results = []
                worker = threading.Thread(target=lambda: results.append(available()))
                worker.start()
                worker.join()
                assert results == [True]
        """,
        "tests/test_process.py": """
            import subprocess
            import sys
            def test_child():
                subprocess.run([sys.executable, "-c", "from shop.child import value; assert value() == 42"], check=True)
        """,
        "tests/test_smoke.py": "def test_smoke(): assert True\n",
        "tests/test_static.py": """
            from shop.settings import VALUE
            def test_static(): assert VALUE == 1
        """,
        "tests/test_dynamic.py": """
            from importlib import import_module
            def test_dynamic(): assert import_module("shop.settings").VALUE == 1
        """,
    }
    for name, content in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip())
