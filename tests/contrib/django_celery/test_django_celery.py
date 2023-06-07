from os.path import dirname
from os.path import sep
import subprocess

from tests.utils import call_program


def test_django_celery_gevent_startup():

    try:
        out, err, retcode, _ = call_program(
            "ddtrace-run",
            "celery",
            "-A",
            "proj",
            "worker",
            "--pool=gevent",
            cwd=sep.join((dirname(__file__), "app")),
            timeout=3,
        )
    except subprocess.TimeoutExpired as celery:
        out = celery.stdout.decode("utf-8")
        err = celery.stderr.decode("utf-8")
        assert "celery@" in out, "Celery started correctly"
        assert "DJANGO_SETTINGS_MODULE" not in err, "No Django lazy objects"
    else:
        assert retcode == 0, "Celery finished too quickly, out: %s err: %s" % (out, err)
