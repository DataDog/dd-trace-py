import os
import sys
import types
import unittest
from importlib.machinery import SourceFileLoader


class _FakeConnection:
    def cursor(self):
        return object()

    def close(self):
        pass


class _FakePsycopg2:
    def __init__(self, recorder):
        self._recorder = recorder

    def connect(self, **kwargs):
        self._recorder["psycopg2"] = kwargs
        return _FakeConnection()


class _FakePyMySQL:
    def __init__(self, recorder):
        self._recorder = recorder

    def connect(self, **kwargs):
        self._recorder["pymysql"] = kwargs
        return _FakeConnection()


def _with_env(env):
    keys = [
        "TEST_POSTGRES_HOST",
        "TEST_POSTGRES_PORT",
        "TEST_POSTGRES_DB",
        "TEST_POSTGRES_USER",
        "TEST_POSTGRES_PASSWORD",
        "TEST_MYSQL_HOST",
        "TEST_MYSQL_PORT",
        "TEST_MYSQL_DB",
        "TEST_MYSQL_USER",
        "TEST_MYSQL_PASSWORD",
    ]
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        if k in env and env[k] is not None:
            os.environ[k] = env[k]
        else:
            os.environ.pop(k, None)
    return saved


def _restore_env(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _load_db_utils(recorder):
    psy_backup = sys.modules.get("psycopg2")
    mysql_backup = sys.modules.get("pymysql")
    sys.modules["psycopg2"] = _FakePsycopg2(recorder)
    sys.modules["pymysql"] = _FakePyMySQL(recorder)
    try:
        path = os.path.join(os.path.dirname(__file__), "db_utils.py")
        name = f"db_utils_test_mod_{id(recorder)}"
        loader = SourceFileLoader(name, path)
        mod = types.ModuleType(loader.name)
        loader.exec_module(mod)
        return mod
    finally:
        if psy_backup is not None:
            sys.modules["psycopg2"] = psy_backup
        else:
            sys.modules.pop("psycopg2", None)
        if mysql_backup is not None:
            sys.modules["pymysql"] = mysql_backup
        else:
            sys.modules.pop("pymysql", None)


class TestDBUtilsEnv(unittest.TestCase):
    def test_psycopg2_uses_env_credentials(self):
        saved = _with_env(
            {
                "TEST_POSTGRES_HOST": "10.0.0.2",
                "TEST_POSTGRES_PORT": "55432",
                "TEST_POSTGRES_DB": "db1",
                "TEST_POSTGRES_USER": "u1",
                "TEST_POSTGRES_PASSWORD": "p1",
            }
        )
        try:
            recorder = {}
            mod = _load_db_utils(recorder)
            conn = mod.get_psycopg2_connection()
            self.assertIsNotNone(conn)
            self.assertIn("psycopg2", recorder)
            params = recorder["psycopg2"]
            self.assertEqual(params["host"], "10.0.0.2")
            self.assertEqual(params["port"], 55432)
            self.assertEqual(params["database"], "db1")
            self.assertEqual(params.get("user"), "u1")
            self.assertEqual(params.get("password"), "p1")
            self.assertIn("options", params)
        finally:
            _restore_env(saved)

    def test_psycopg2_omits_credentials_when_not_provided(self):
        saved = _with_env(
            {
                "TEST_POSTGRES_HOST": "127.0.0.1",
                "TEST_POSTGRES_PORT": "5432",
                "TEST_POSTGRES_DB": "postgres",
                "TEST_POSTGRES_USER": None,
                "TEST_POSTGRES_PASSWORD": None,
            }
        )
        try:
            recorder = {}
            mod = _load_db_utils(recorder)
            mod.get_psycopg2_connection()
            params = recorder["psycopg2"]
            self.assertNotIn("user", params)
            self.assertNotIn("password", params)
        finally:
            _restore_env(saved)

    def test_pymysql_uses_env_credentials(self):
        saved = _with_env(
            {
                "TEST_MYSQL_HOST": "10.0.0.3",
                "TEST_MYSQL_PORT": "3307",
                "TEST_MYSQL_DB": "db2",
                "TEST_MYSQL_USER": "mu",
                "TEST_MYSQL_PASSWORD": "mp",
            }
        )
        try:
            recorder = {}
            mod = _load_db_utils(recorder)
            conn = mod.get_pymysql_connection()
            self.assertIsNotNone(conn)
            params = recorder["pymysql"]
            self.assertEqual(params["host"], "10.0.0.3")
            self.assertEqual(params["port"], 3307)
            self.assertEqual(params["database"], "db2")
            self.assertEqual(params.get("user"), "mu")
            self.assertEqual(params.get("password"), "mp")
        finally:
            _restore_env(saved)

    def test_pymysql_omits_credentials_when_not_provided(self):
        saved = _with_env(
            {
                "TEST_MYSQL_HOST": "127.0.0.1",
                "TEST_MYSQL_PORT": "3306",
                "TEST_MYSQL_DB": "test",
                "TEST_MYSQL_USER": None,
                "TEST_MYSQL_PASSWORD": None,
            }
        )
        try:
            recorder = {}
            mod = _load_db_utils(recorder)
            mod.get_pymysql_connection()
            params = recorder["pymysql"]
            self.assertNotIn("user", params)
            self.assertNotIn("password", params)
        finally:
            _restore_env(saved)


if __name__ == "__main__":
    unittest.main()
