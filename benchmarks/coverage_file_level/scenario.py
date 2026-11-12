import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import typing as t

import bm


class CoverageFileLevel(bm.Scenario):  # type: ignore[misc]
    """Measure file-level coverage over a generated, multi-module corpus.

    The timed operation is a fresh Python process that installs the internal
    collector, imports the corpus, and runs many test-like coverage contexts.
    Corpus generation stays outside the timed region.
    """

    ncontexts: int
    nmodules: int
    functions_per_module: int
    modules_per_context: int
    collect_coverage: bool

    # The measured work happens in a child process.
    cprofile_loops: int = 0

    def run(self) -> t.Iterator[t.Callable[[int], None]]:
        workdir = tempfile.mkdtemp(prefix="ddbench_coverage_file_level_")
        corpus = os.path.join(workdir, "corpus")
        os.makedirs(corpus, exist_ok=True)

        for module_index in range(self.nmodules):
            module_path = os.path.join(corpus, "bench_module_{}.py".format(module_index))
            with open(module_path, "w") as module_file:
                function_names = []
                for function_index in range(self.functions_per_module):
                    function_name = "exercise_{}".format(function_index)
                    function_names.append(function_name)
                    adjustment = module_index + function_index + 1
                    module_file.write(
                        "def {}(value):\n"
                        "    if value & 1:\n"
                        "        return value + {}\n"
                        "    return value - {}\n\n".format(function_name, adjustment, adjustment)
                    )
                module_file.write("FUNCTIONS = ({},)\n".format(", ".join(function_names)))

        env = os.environ.copy()
        env.update(
            {
                "_DD_COVERAGE_FILE_LEVEL": "true",
                "_DD_COVERAGE_ACCURATE_IMPORTS": "false",
            }
        )

        runner = os.path.join(os.path.dirname(__file__), "runner.py")
        args = [
            sys.executable,
            runner,
            corpus,
            str(self.ncontexts),
            str(self.nmodules),
            str(self.modules_per_context),
        ]
        if self.collect_coverage:
            args.append("--coverage")

        def _(loops: int) -> None:
            for _ in range(loops):
                result = subprocess.run(  # nosec B603
                    args,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        "coverage benchmark exited {}:\n{}".format(
                            result.returncode, result.stderr.decode(errors="replace")[-2000:]
                        )
                    )

        try:
            yield _
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
