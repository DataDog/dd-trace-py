from collections.abc import Callable
from collections.abc import Generator
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404
import sys
import tempfile

import bm


class CoverageMonitoring(bm.Scenario):  # type: ignore[misc]
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
    file_level_coverage: bool

    # The measured work happens in a child process.
    cprofile_loops: int = 0

    def run(self) -> Generator[Callable[[int], None], None, None]:
        workdir = Path(tempfile.mkdtemp(prefix="ddbench_coverage_monitoring_"))
        corpus = workdir / "corpus"
        corpus.mkdir(parents=True, exist_ok=True)

        for module_index in range(self.nmodules):
            module_path = corpus / f"bench_module_{module_index}.py"
            with module_path.open("w") as module_file:
                function_names = []
                for function_index in range(self.functions_per_module):
                    function_name = f"exercise_{function_index}"
                    function_names.append(function_name)
                    adjustment = module_index + function_index + 1
                    module_file.write(
                        f"def {function_name}(value):\n"
                        "    if value & 1:\n"
                        f"        return value + {adjustment}\n"
                        f"    return value - {adjustment}\n\n"
                    )
                module_file.write(f"FUNCTIONS = ({', '.join(function_names)},)\n")

        env = os.environ.copy()
        env.update(
            {
                "_DD_COVERAGE_FILE_LEVEL": "true" if self.file_level_coverage else "false",
                "_DD_COVERAGE_ACCURATE_IMPORTS": "false",
            }
        )

        args = [
            sys.executable,
            str(Path(__file__).with_name("runner.py")),
            str(corpus),
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
                        f"coverage benchmark exited {result.returncode}:\n"
                        f"{result.stderr.decode(errors='replace')[-2000:]}"
                    )

        try:
            yield _
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
