from collections import defaultdict
from io import BytesIO

import bm
import bm.utils as utils

from ddtrace.contrib.trace_utils import set_http_meta


class Config(defaultdict):
    def __getattribute__(self, item):
        return self[item]


COMMON_DJANO_META = {
    "PATH": "",
    "LC_MEASUREMENT": "es_ES.UTF-8",
    "INVOCATION_ID": "159f1f11bb854f56bd878487c323cadd",
    "XMODIFIERS": "@im=ibus",
    "LC_TELEPHONE": "es_ES.UTF-8",
    "XDG_DATA_DIRS": "/usr/share/ubuntu:/usr/local/share/:/usr/share/:/var/lib/snapd/desktop",
    "GDMSESSION": "ubuntu",
    "LC_TIME": "es_ES.UTF-8",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    "IDE_PROJECT_ROOTS": "//projects/agent",
    "PS1": "(venv) ",
    "XDG_CURRENT_DESKTOP": "ubuntu:GNOME",
    "JOURNAL_STREAM": "8:57546",
    "SSH_AGENT_PID": "3048",
    "LC_PAPER": "es_ES.UTF-8",
    "SESSION_MANAGER": "local/dd:@/tmp/.ICE-unix/3093,unix/dd:/tmp/.ICE-unix/3093",
    "PWD": "/dd/showcase-django/showcase-django",
    "MANAGERPID": "2851",
    "IM_CONFIG_PHASE": "1",
    "PYCHARM_HOSTED": "1",
    "GJS_DEBUG_TOPICS": "JS ERROR;JS LOG",
    "PYTHONPATH": "/dd/testapp_django/py36-39-dj2.2-3.2:/dd/venv:/dd/testapp_flask/py27-39_flask1.1.4",
    "SHELL": "/usr/bin/zsh",
    "LC_ADDRESS": "es_ES.UTF-8",
    "GIO_LAUNCHED_DESKTOP_FILE": "/home/dd/.local/share/applications/jetbrains-pycharm.desktop",
    "PYENV_ROOT": "/home/dd/.pyenv",
    "VIRTUAL_ENV": "/dd/venv",
    "XDG_SESSION_DESKTOP": "ubuntu",
    "SHLVL": "0",
    "LC_IDENTIFICATION": "es_ES.UTF-8",
    "LC_MONETARY": "es_ES.UTF-8",
    "QT_IM_MODULE": "ibus",
    "TERM": "xterm-256color",
    "XDG_CONFIG_DIRS": "/etc/xdg/xdg-ubuntu:/etc/xdg",
    "LANG": "en_US.UTF-8",
    "XDG_SESSION_TYPE": "x11",
    "PYDEVD_LOAD_VALUES_ASYNC": "True",
    "LC_NAME": "es_ES.UTF-8",
    "PYCHARM_DISPLAY_PORT": "63342",
    "XDG_SESSION_CLASS": "user",
    "_": "/usr/bin/dbus-update-activation-environment",
    "PYTHONIOENCODING": "UTF-8",
    "GPG_AGENT_INFO": "/run/user/1000/gnupg/S.gpg-agent:0:1",
    "DESKTOP_SESSION": "ubuntu",
    "XDG_MENU_PREFIX": "gnome-",
    "GIO_LAUNCHED_DESKTOP_FILE_PID": "7043",
    "QT_ACCESSIBILITY": "1",
    "WINDOWPATH": "2",
    "PYTHONDONTWRITEBYTECODE": "1",
    "LC_NUMERIC": "es_ES.UTF-8",
    "GJS_DEBUG_OUTPUT": "stderr",
    "SSH_AUTH_SOCK": "/run/user/1000/keyring/ssh",
    "PYTHONUNBUFFERED": "1",
    "GNOME_SHELL_SESSION_MODE": "ubuntu",
    "XDG_RUNTIME_DIR": "/run/user/1000",
    "HOME": "/home/dd",
    "DJANGO_SETTINGS_MODULE": "showcase.settings",
    "TZ": "UTC",
    "SERVER_NAME": "localhost",
    "GATEWAY_INTERFACE": "CGI/1.1",
    "SERVER_PORT": "8000",
    "REMOTE_HOST": "",
    "CONTENT_LENGTH": "",
    "SCRIPT_NAME": "",
    "SERVER_PROTOCOL": "HTTP/1.1",
    "SERVER_SOFTWARE": "WSGIServer/0.2",
    "REQUEST_METHOD": "GET",
    "PATH_INFO": "/",
    "QUERY_STRING": "func=subprocess.run&cmd=%2Fbin%2Fecho+hello",
    "REMOTE_ADDR": "127.0.0.1",
    "CONTENT_TYPE": "text/plain",
    "HTTP_HOST": "localhost:8000",
    "HTTP_CONNECTION": "keep-alive",
    "HTTP_CACHE_CONTROL": "max-age=0",
    "HTTP_SEC_CH_UA": '"Chromium";v="92", " Not A;Brand";v="99", "Google Chrome";v="92"',
    "HTTP_SEC_CH_UA_MOBILE": "?0",
    "HTTP_UPGRADE_INSECURE_REQUESTS": "1",
    "HTTP_USER_AGENT": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/92.0.4515.159 Safari/537.36",
    "HTTP_ACCEPT": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
    "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    "HTTP_SEC_FETCH_SITE": "none",
    "HTTP_SEC_FETCH_MODE": "navigate",
    "HTTP_SEC_FETCH_USER": "?1",
    "HTTP_SEC_FETCH_DEST": "document",
    "HTTP_ACCEPT_ENCODING": "gzip, deflate, br",
    "HTTP_ACCEPT_LANGUAGE": "en-US,en;q=0.9",
    "HTTP_COOKIE": "Pycharm-45729245=449f1b16-fe0a-4623-92bc-418ec418ed4b; Idea-9fdb9ed8="
    "448d4c93-863c-4e9b-a8e7-bbfbacd073d2; csrftoken=cR8TVoVebF2afssCR16pQeqHcxA"
    "lA3867P6zkkUBYDL5Q92kjSGtqptAry1htdlL; _xsrf=2|d4b85683|7e2604058ea673d12dc6604f"
    '96e6e06d|1635869800; username-localhost-8888="2|1:0|10:1637328584|23:username-loca'
    "lhost-8888|44:OWNiOTFhMjg1NDllNDQxY2I2Y2M2ODViMzRjMTg3NGU=|3bc68f938dcc081a9a02e51660"
    '0c0d38b14a3032053a7e16b180839298e25b42"',
    "wsgi.input": BytesIO(),
    "wsgi.url_scheme": "http",
}

COOKIES = {"csrftoken": "cR8TVoVebF2afssCR16pQeqHcxAlA3867P6zkkUBYDL5Q92kjSGtqptAry1htdlL"}

DATA_GET = dict(
    method="GET",
    url="http://localhost:8888/test-benchmark",
    status_code=200,
    status_msg="OK",
    query=b"key1=value1&key2=value2",
    parsed_query={"key1": "value1", "key2": "value2"},
    request_headers=COMMON_DJANO_META,
    response_headers=COMMON_DJANO_META,
    retries_remain=0,
    raw_uri="http://127.0.0.1:8081/test/1/?key1=value1&key2=value2",
    request_cookies=COOKIES,
    request_path_params={"id": 1},
    request_body=None,  # type: Optional[Union[str, Dict[str, List[str]]]]
)


class SetHttpMeta(bm.Scenario):
    nspans = bm.var(type=int)
    allenabled = bm.var_bool()

    def run(self):
        # run scenario to also set tags on spans
        span = utils.gen_span(str(self))
        config_all_enabled = Config(lambda: True)
        config_all_disabled = Config(lambda: False)

        def _(loops):
            for _ in range(loops):
                if self.allenabled:
                    set_http_meta(span, config_all_enabled, **DATA_GET)
                else:
                    set_http_meta(span, config_all_disabled, **DATA_GET)

        yield _
