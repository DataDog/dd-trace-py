from flask import session
import flask_login
from flask_login import LoginManager
from flask_login import UserMixin
from flask_login import current_user
import pytest
from werkzeug.security import check_password_hash
from werkzeug.security import generate_password_hash

from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec.trace_utils import track_user_login_failure_event
from ddtrace.contrib.flask_login.patch import patch as patch_login
from ddtrace.contrib.flask_login.patch import unpatch as unpatch_login
from ddtrace.contrib.sqlite3.patch import patch
from ddtrace.ext import user
from tests.contrib.flask import BaseFlaskTestCase
from tests.contrib.patch import emit_integration_and_version_to_test_agent
from tests.utils import override_global_config


class User(UserMixin):
    def __init__(self, _id, login, name, email, password, is_admin=False):
        self.id = _id
        self.login = login
        self.name = name
        self.email = email
        self.password = generate_password_hash(password)
        self.is_admin = is_admin

    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)

    def __repr__(self):
        return "<User {}>".format(self.email)

    def get_id(self):
        return self.id


TEST_USER = "john"
TEST_USER_NAME = "John Tester"
TEST_EMAIL = "john@test.com"
TEST_PASSWD = "passw0rd"

_USERS = [User(1, TEST_USER, TEST_USER_NAME, TEST_EMAIL, TEST_PASSWD, False)]

EMPTY_USER = User(-1, "", "", "", "", False)


def get_user(email):
    for _user in _USERS:
        if _user.email == email:
            return _user
    return None


def get_response_body(response):
    if hasattr(response, "text"):
        return response.text
    return response.data.decode("utf-8")


class FlaskLoginAppSecTestCase(BaseFlaskTestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog):
        self._caplog = caplog

    def setUp(self):
        super(FlaskLoginAppSecTestCase, self).setUp()
        patch()
        # flask_login stuff
        self.app.config[
            "SECRET_KEY"
        ] = "7110c8ae51a4b5af97be6534caef90e4bb9bdcb3380af008f90b23a5d1616bf319bc298105da20fe"
        login_manager = LoginManager(self.app)

        def load_user(user_id):
            for _user in _USERS:
                if _user.id == int(user_id):
                    return _user
            return None

        login_manager._user_callback = load_user

    def _aux_appsec_prepare_tracer(self, appsec_enabled=True):
        self.tracer._asm_enabled = appsec_enabled
        # Hack: need to pass an argument to configure so that the processors are recreated
        self.tracer.configure(api_version="v0.4")

    def _login_base(self, email, passwd):
        if current_user and current_user.is_authenticated:
            return "Already authenticated"

        _user = get_user(email)
        if _user is None:
            flask_login.login_user(EMPTY_USER, remember=False)
            return "User not found"

        if _user.check_password(passwd):
            flask_login.login_user(_user, remember=False)
            return "User %s logged in successfully, session: %s" % (TEST_USER, session["_id"])
        else:
            track_user_login_failure_event(self.tracer, user_id=_user.id, exists=True)

        return "Authentication failure"

    def test_flask_login_events_disabled_explicitly(self):
        @self.app.route("/login")
        def login():
            self._login_base(TEST_EMAIL, TEST_PASSWD)
            _user = User(1, TEST_USER, TEST_USER_NAME, TEST_EMAIL, TEST_PASSWD, False)
            return str(current_user == _user)

        try:
            with override_global_config(dict(_asm_enabled=True, _automatic_login_events_mode="disabled")):
                self._aux_appsec_prepare_tracer()
                resp = self.client.get("/login")
                assert resp.status_code == 200
                assert resp.data == b"True"
                root_span = self.pop_spans()[0]
                assert not root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.track")
                assert not root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".failure.track")
        finally:
            unpatch_login()

    def test_flask_login_events_disabled_noappsec(self):
        @self.app.route("/login")
        def login():
            self._login_base(TEST_EMAIL, TEST_PASSWD)
            _user = User(1, TEST_USER, TEST_USER_NAME, TEST_EMAIL, TEST_PASSWD, False)
            return str(current_user == _user)

        try:
            with override_global_config(dict(_asm_enabled=False, _automatic_login_events_mode="safe")):
                self._aux_appsec_prepare_tracer()
                resp = self.client.get("/login")
                assert resp.status_code == 200
                assert resp.data == b"True"
                root_span = self.pop_spans()[0]
                assert not root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.track")
                assert not root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".failure.track")
        finally:
            unpatch_login()

    def test_flask_login_sucess_extended(self):
        @self.app.route("/login")
        def login():
            self._login_base(TEST_EMAIL, TEST_PASSWD)
            _user = User(1, TEST_USER, TEST_USER_NAME, TEST_EMAIL, TEST_PASSWD, False)
            return str(current_user == _user)

        try:
            with override_global_config(dict(_asm_enabled=True, _automatic_login_events_mode="extended")):
                self._aux_appsec_prepare_tracer()
                patch_login()
                resp = self.client.get("/login")
                assert resp.status_code == 200
                assert resp.data == b"True"
                root_span = self.pop_spans()[0]
                assert root_span.get_tag(user.ID) == "1"
                assert root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".success.track") == "true"
                assert root_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == "extended"
                assert root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.login") == TEST_USER
                assert root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.email") == TEST_EMAIL
                assert root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.username") == TEST_USER_NAME
        finally:
            unpatch_login()

    def test_flask_login_sucess_safe(self):
        @self.app.route("/login")
        def login():
            self._login_base(TEST_EMAIL, TEST_PASSWD)
            _user = User(1, TEST_USER, TEST_USER_NAME, TEST_EMAIL, TEST_PASSWD, False)
            return str(current_user == _user)

        try:
            with override_global_config(dict(_asm_enabled=True, _automatic_login_events_mode="safe")):
                self._aux_appsec_prepare_tracer()
                patch_login()
                resp = self.client.get("/login")
                assert resp.status_code == 200
                assert resp.data == b"True"
                root_span = self.pop_spans()[0]
                assert root_span.get_tag(user.ID) == "1"
                assert root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".success.track") == "true"
                assert root_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == "safe"
                assert not root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".success.login")
                assert not root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".success.email")
                assert not root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".success.username")
        finally:
            unpatch_login()

    def test_flask_login_sucess_safe_is_default_if_wrong(self):
        @self.app.route("/login")
        def login():
            self._login_base(TEST_EMAIL, TEST_PASSWD)
            _user = User(1, TEST_USER, TEST_USER_NAME, TEST_EMAIL, TEST_PASSWD, False)
            return str(current_user == _user)

        try:
            with override_global_config(dict(_asm_enabled=True, _automatic_login_events_mode="foobar")):
                self._aux_appsec_prepare_tracer()
                patch_login()
                resp = self.client.get("/login")
                assert resp.status_code == 200
                assert resp.data == b"True"
                root_span = self.pop_spans()[0]
                assert root_span.get_tag(user.ID) == "1"
        finally:
            unpatch_login()

    def test_flask_login_sucess_safe_is_default_if_missing(self):
        @self.app.route("/login")
        def login():
            self._login_base(TEST_EMAIL, TEST_PASSWD)
            _user = User(1, TEST_USER, TEST_USER_NAME, TEST_EMAIL, TEST_PASSWD, False)
            return str(current_user == _user)

        try:
            with override_global_config(dict(_asm_enabled=True)):
                self._aux_appsec_prepare_tracer()
                patch_login()
                resp = self.client.get("/login")
                assert resp.status_code == 200
                assert resp.data == b"True"
                root_span = self.pop_spans()[0]
                assert root_span.get_tag(user.ID) == "1"
        finally:
            unpatch_login()

    def test_flask_login_failure_user_doesnt_exists(self):
        @self.app.route("/login")
        def login():
            return self._login_base("mike@test.com", TEST_PASSWD)

        try:
            with override_global_config(dict(_asm_enabled=True, _automatic_login_events_mode="extended")):
                self._aux_appsec_prepare_tracer()
                patch_login()
                resp = self.client.get("/login")
                assert resp.status_code == 200
                assert resp.data == b"User not found"
                root_span = self.pop_spans()[0]
                print(root_span)
                assert root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure.track") == "true"
                assert root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure." + user.ID) == "missing"
                assert root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure." + user.EXISTS) == "false"
        finally:
            unpatch_login()

    def test_flask_login_failure_wrong_password(self):
        @self.app.route("/login")
        def login():
            return self._login_base(TEST_EMAIL, "hacker")

        try:
            with override_global_config(dict(_asm_enabled=True, _automatic_login_events_mode="safe")):
                self._aux_appsec_prepare_tracer()
                patch_login()
                resp = self.client.get("/login")
                assert resp.status_code == 200
                assert resp.data == b"Authentication failure"
                root_span = self.pop_spans()[0]
                assert root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure.track") == "true"
                assert root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure." + user.ID) == "1"
                assert root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure." + user.EXISTS) == "true"
        finally:
            unpatch_login()

    def test_flask_login_sucess_safe_but_user_set_login_field(self):
        @self.app.route("/login")
        def login():
            self._login_base(TEST_EMAIL, TEST_PASSWD)
            _user = User(1, TEST_USER, TEST_USER_NAME, TEST_EMAIL, TEST_PASSWD, False)
            return str(current_user == _user)

        try:
            with override_global_config(
                dict(_asm_enabled=True, _user_model_login_field="login", _automatic_login_events_mode="safe")
            ):
                self._aux_appsec_prepare_tracer()
                patch_login()
                resp = self.client.get("/login")
                assert resp.status_code == 200
                assert resp.data == b"True"
                root_span = self.pop_spans()[0]
                assert root_span.get_tag(user.ID) == TEST_USER
                assert root_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".success.track") == "true"
                assert root_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == "safe"
        finally:
            unpatch_login()

    def test_and_emit_get_version(self):
        from ddtrace.contrib.flask_login import get_version

        version = get_version()
        assert type(version) == str
        assert version != ""

        emit_integration_and_version_to_test_agent("flask_login", version)
