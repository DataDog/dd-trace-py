from os.path import dirname
from os.path import sep
import subprocess

from tests.utils import call_program


def test_django_celery_gevent_startup():
    """Test that Celery starts correctly with the Django integration enabled.

    If the Django integration force-loads some modules while patching, it is
    likely that we might see lazy objects, like settings, being created before
    time. This would cause Celery to trigger exceptions, causing the application
    to fail to start.

    In this particular instance we test that the application starts correctly
    (albeit with no message broker running) and that we don't get any errors
    about Django settings.
    """
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
        err_text = err.decode("utf-8")
        if "not recommended" not in err_text:
            assert retcode == 0, "Celery was finished with errors: %s" % err_text
