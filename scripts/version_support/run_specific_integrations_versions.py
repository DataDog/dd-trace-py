"""Rewrite selected Riot integration suites to specific package versions.

This helper parses `riotfile.py`, updates the hardcoded integration package
version lists for the targeted suites, and writes the rewritten Riot config
back to disk.
"""

from pathlib import Path
from .parsing import collect_venvs
from .rewriting import regenerate_suite


def run() -> int:
    riotfile_path = (Path(__file__).resolve().parent.parent.parent / "riotfile.py").resolve()
    hardcoded_versions: dict[str, list[str]] = {"aiokafka": ["prout1"], "mako": ["prout2"]}

    riotfile_source: str = riotfile_path.read_text(encoding="utf-8")
    riotfile_filename = str(riotfile_path)
    suites = collect_venvs(riotfile_source, riotfile_filename)

    rewritten_source = riotfile_source
    for suite_name, versions in hardcoded_versions.items():
        suite = suites.get(suite_name)
        if suite is None:
            raise RuntimeError(f"Unable to find suite {suite_name!r} in riotfile.py")
        rewritten_source = regenerate_suite(
            rewritten_source,
            suite=suite,
            suite_name=suite_name,
            versions=versions,
        )
        suites = collect_venvs(rewritten_source, str(riotfile_path))

    riotfile_path.write_text(rewritten_source, encoding="utf-8")
    print(f"Rewrote suites: {', '.join(hardcoded_versions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
