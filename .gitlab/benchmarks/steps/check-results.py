#!/usr/bin/env python3
"""Require measured results for every configured benchmark before publishing them."""

import argparse
import json
import math
from pathlib import Path
import sys

import yaml


def validate_results(config_path: Path, result_directories: list[Path]) -> None:
    configurations = yaml.safe_load(config_path.read_text())
    if not isinstance(configurations, dict) or not configurations:
        raise ValueError(f"{config_path}: expected nonempty benchmark configurations")
    for directory in result_directories:
        for configuration in configurations:
            path = directory / f"results.{configuration}.json"
            try:
                result = json.loads(path.read_text())
                benchmarks = result.get("benchmarks", [])
                if len(benchmarks) != 1:
                    raise ValueError("expected one measured benchmark")
                benchmark = benchmarks[0]
                metadata = {**result.get("metadata", {}), **benchmark.get("metadata", {})}
                if not metadata.get("name", "").endswith(f"-{configuration}"):
                    raise ValueError("benchmark name does not match the configuration")
                values = [value for run in benchmark.get("runs", []) for value in run.get("values", [])]
                if not values or any(
                    type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in values
                ):
                    raise ValueError("expected finite, positive measurements; warmups alone are insufficient")
            except (OSError, ValueError, TypeError, AttributeError) as error:
                raise ValueError(f"{path}: {error}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("results", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        validate_results(args.config, args.results)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
