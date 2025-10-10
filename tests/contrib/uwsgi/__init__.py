import os
import platform
import shutil
import subprocess
import sys


def run_uwsgi(cmd):
    def _run(*args):
        env = os.environ.copy()
        # Print ldd output for debugging on Linux
        if platform.system() == "Linux":
            uwsgi_path = shutil.which("uwsgi")
            if not uwsgi_path:
                raise FileNotFoundError("uwsgi not found")
            try:
                ldd_output = subprocess.run(["ldd", uwsgi_path], capture_output=True, text=True)
                print(f"ldd output for {uwsgi_path}:")
                print(ldd_output.stdout)
                if ldd_output.stderr:
                    print("ldd stderr:", ldd_output.stderr)
            except Exception as e:
                print(f"Failed to run ldd: {e}")

        python_libdir = os.path.join(sys.base_prefix, "lib")
        if "LD_LIBRARY_PATH" in env:
            # Prepend Python lib directory to existing LD_LIBRARY_PATH
            env["LD_LIBRARY_PATH"] = f"{python_libdir}:{env['LD_LIBRARY_PATH']}"
        else:
            env["LD_LIBRARY_PATH"] = python_libdir

        return subprocess.Popen(cmd + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)

    return _run
