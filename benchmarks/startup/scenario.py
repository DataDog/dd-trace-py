import os
import subprocess
import tempfile

import bm


class Startup(bm.Scenario):
    do_import = bm.var_bool()

    def run(self):
        # setup subprocess environment variables
        with tempfile.NamedTemporaryFile(delete=False) as code_file:
            if self.do_import:
                code_file.write(b"import ddtrace\n")

        def _(loops):
            for _ in range(loops):
                subprocess.check_output(["python", code_file.name], env=os.environ.copy())

        yield _
