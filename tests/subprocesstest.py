"""
subprocesstest enables unittest test cases and suites to be run in separate
python interpreter instances.

A base class SubprocessTestCase is provided that, when extended, will run test
cases marked with @run_in_subprocess in a separate python interpreter.
"""
import inspect
import os
import subprocess
import sys
import unittest


SUBPROC_TEST_ATTR = "_subproc_test"
SUBPROC_TEST_ENV_ATTR = "_subproc_test_env"
SUBPROC_ENV_VAR = "SUBPROCESS_TEST"
SUBPROC_USE_PYTEST = "_use_pytest"


def run_in_subprocess(*args, **kwargs):
    """
    Marks a test case that is to be run in its own 'clean' interpreter instance.

    When applied to a SubprocessTestCase class, each method will be run in a
    separate interpreter instance.

    When applied only to a method of a SubprocessTestCase, only the method
    will be run in a separate interpreter instance.

    Usage on a class::

        from tests.subprocesstest import SubprocessTestCase, run_in_subprocess

        @run_in_subprocess
        class PatchTests(SubprocessTestCase):
            # will be run in new interpreter
            def test_patch_before_import(self):
                patch()
                import module

            # will be run in new interpreter as well
            def test_patch_after_import(self):
                import module
                patch()


    Usage on a test method::

        class OtherTests(SubprocessTestCase):
            @run_in_subprocess
            def test_case(self):
                # Run in subprocess
                pass

            def test_case(self):
                # NOT run in subprocess
                pass

    :param env_override: dict of environment variables to provide to the subprocess.
    :return:
    """
    env_overrides = kwargs.get("env_overrides")
    use_pytest = kwargs.get("use_pytest")

    def wrapper(obj):
        setattr(obj, SUBPROC_TEST_ATTR, True)
        if env_overrides is not None:
            setattr(obj, SUBPROC_TEST_ENV_ATTR, env_overrides)
            setattr(obj, SUBPROC_USE_PYTEST, use_pytest)
        return obj

    # Support both @run_in_subprocess and @run_in_subprocess(env_overrides=...) usage
    if len(args) == 1 and callable(args[0]):
        return wrapper(args[0])
    else:
        return wrapper


class SubprocessTestCase(unittest.TestCase):
    run_in_subprocess = staticmethod(run_in_subprocess)

    def _full_method_name(self, use_pytest=False):
        test = getattr(self, self._testMethodName)
        # DEV: we have to use the internal self reference of the bound test
        # method to pull out the class and module since using a mix of `self`
        # and the test attributes will result in inconsistencies when the test
        # method is defined on another class.
        # A concrete case of this is a parent and child TestCase where the child
        # doesn't override a parent test method. The full_method_name we want
        # is that of the child test method (even though it exists on the parent).
        # This is only true if the test method is bound by pytest; pytest>=5.4 returns a function.
        if inspect.ismethod(test):
            modpath = test.__self__.__class__.__module__
            clsname = test.__self__.__class__.__name__
        else:
            modpath = self.__class__.__module__
            clsname = self.__class__.__name__
        testname = test.__name__
        format_string = "{}.py::{}::{}" if use_pytest else "{}.{}.{}"
        modpath = modpath.replace(".", "/" if use_pytest else ".")
        testcase_name = format_string.format(modpath, clsname, testname)
        return testcase_name

    def _run_test_in_subprocess(self, result):
        # Copy the environment and include the special subprocess environment
        # variable for the subprocess to detect.
        env_overrides = self._get_env_overrides()
        use_pytest = self._get_use_pytest()
        full_testcase_name = self._full_method_name(use_pytest=use_pytest)
        sp_test_env = os.environ.copy()
        sp_test_env.update(env_overrides)
        sp_test_env[SUBPROC_ENV_VAR] = "True"
        test_framework = "pytest" if use_pytest else "unittest"
        sp_test_cmd = ["python", "-m", test_framework, full_testcase_name]
        sp = subprocess.Popen(
            sp_test_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=sp_test_env,
        )
        stdout, stderr = sp.communicate()
        stdout = stdout.decode()
        stderr = stderr.decode()

        if sp.returncode and "_pytest.outcomes.xfailed" not in stderr.lower():
            try:
                cmdf = " ".join(sp_test_cmd)
                msg = f'Subprocess Test "{cmdf}" Failed (exit code {sp.returncode}):\n{stderr}'
                raise Exception(msg)
            except Exception:
                exc_info = sys.exc_info()

            # DEV: stderr, stdout are byte sequences so to print them nicely
            #      back out they should be decoded.
            sys.stderr.write(stderr)
            sys.stdout.write(stdout)
            result.addFailure(self, exc_info)
        else:
            result.addSuccess(self)

    def _in_subprocess(self):
        """Determines if the test is being run in a subprocess.

        This is done by checking for an environment variable that we call the
        subprocess test with.

        :return: whether the test is a subprocess test
        """
        return os.getenv(SUBPROC_ENV_VAR, None) is not None

    def _is_subprocess_test(self):
        if hasattr(self, SUBPROC_TEST_ATTR):
            return True

        test = getattr(self, self._testMethodName)
        if hasattr(test, SUBPROC_TEST_ATTR):
            return True

        return False

    def _get_env_overrides(self):
        if hasattr(self, SUBPROC_TEST_ENV_ATTR):
            return getattr(self, SUBPROC_TEST_ENV_ATTR)

        test = getattr(self, self._testMethodName)
        if hasattr(test, SUBPROC_TEST_ENV_ATTR):
            return getattr(test, SUBPROC_TEST_ENV_ATTR)

        return {}

    def _get_use_pytest(self):
        if hasattr(self, SUBPROC_USE_PYTEST):
            return getattr(self, SUBPROC_USE_PYTEST)

        test = getattr(self, self._testMethodName)
        if hasattr(test, SUBPROC_USE_PYTEST):
            return getattr(test, SUBPROC_USE_PYTEST)

        return False

    def run(self, result=None):
        if not self._is_subprocess_test():
            return super(SubprocessTestCase, self).run(result=result)

        if self._in_subprocess():
            return super(SubprocessTestCase, self).run(result=result)
        else:
            self._run_test_in_subprocess(result)
