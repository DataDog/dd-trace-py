#!/usr/bin/env python3
"""Pin a libddwaf release in setup.py: LIBDDWAF_VERSION and the checksum of every bundled archive.

Each checksum is the digest GitHub computed when the archive was uploaded to the release, and must
match the .sha256 asset published next to the archive. setup.py is only rewritten when every archive
it bundles checks out. Set GITHUB_TOKEN to avoid the GitHub API rate limit.
"""

import argparse
import ast
import json
import os
from pathlib import Path
import re
import sys
import typing as t
from urllib.error import URLError
from urllib.request import Request
from urllib.request import urlopen


SETUP_PY = Path(__file__).resolve().parents[1] / "setup.py"
RELEASE_API = "https://api.github.com/repos/DataDog/libddwaf/releases/tags/%s"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?")
SHA256 = re.compile(r"[0-9a-f]{64}")
PINNED = ("LIBDDWAF_VERSION", "LIBDDWAF_CHECKSUMS")

Fetch = t.Callable[[str], bytes]


def request(url: str) -> Request:
    req = Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.getenv("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    return req


def fetch(url: str) -> bytes:
    with urlopen(request(url), timeout=30) as response:  # nosec B310
        return response.read()


def _assignments(source: str) -> dict[str, ast.Assign]:
    nodes = {
        node.targets[0].id: node
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in PINNED
    }
    missing = [name for name in PINNED if name not in nodes]
    if missing:
        raise ValueError(f"setup.py has no {', '.join(missing)}")
    return nodes


def pinned(source: str) -> tuple[str, dict[str, str]]:
    nodes = _assignments(source)
    return ast.literal_eval(nodes["LIBDDWAF_VERSION"].value), ast.literal_eval(nodes["LIBDDWAF_CHECKSUMS"].value)


def archive_names(checksums: dict[str, str], current: str, version: str) -> list[str]:
    prefix = f"libddwaf-{current}-"
    names = []
    for name in checksums:
        if not name.startswith(prefix):
            raise ValueError(f"{name} in LIBDDWAF_CHECKSUMS is not an archive of libddwaf {current}")
        names.append(f"libddwaf-{version}-{name[len(prefix) :]}")
    return names


def release_checksums(version: str, names: list[str], fetch: Fetch = fetch) -> dict[str, str]:
    assets = {asset["name"]: asset for asset in json.loads(fetch(RELEASE_API % version))["assets"]}
    checksums = {}
    for name in names:
        if name not in assets or f"{name}.sha256" not in assets:
            raise ValueError(f"libddwaf {version} has no {name} asset with a .sha256 next to it")
        digest = assets[name].get("digest") or ""
        if not digest.startswith("sha256:") or not SHA256.fullmatch(digest[len("sha256:") :]):
            raise ValueError(f"GitHub reports no SHA-256 digest for {name}")
        published = fetch(assets[f"{name}.sha256"]["browser_download_url"]).decode().split()
        if published != [digest[len("sha256:") :], name] and published != [digest[len("sha256:") :], f"*{name}"]:
            raise ValueError(f"{name}: GitHub digest {digest} does not match its .sha256 asset: {' '.join(published)}")
        checksums[name] = digest[len("sha256:") :]
    return checksums


def updated_setup_py(source: str, version: str, checksums: dict[str, str]) -> str:
    replacements = {
        "LIBDDWAF_VERSION": f'LIBDDWAF_VERSION = "{version}"\n',
        "LIBDDWAF_CHECKSUMS": "LIBDDWAF_CHECKSUMS = {\n"
        + "".join(f'    "{name}": "{checksum}",\n' for name, checksum in checksums.items())
        + "}\n",
    }
    lines = source.splitlines(keepends=True)
    nodes = _assignments(source)
    for name in sorted(nodes, key=lambda name: nodes[name].lineno, reverse=True):
        lines[nodes[name].lineno - 1 : nodes[name].end_lineno] = [replacements[name]]
    return "".join(lines)


def main(argv: t.Optional[list[str]] = None, fetch: Fetch = fetch, setup_py: Path = SETUP_PY) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="the libddwaf release to pin, for example 2.2.0")
    args = parser.parse_args(argv)
    if not VERSION.fullmatch(args.version):
        parser.error(f"invalid libddwaf version: {args.version}")

    source = setup_py.read_text()
    try:
        current, checksums = pinned(source)
        names = archive_names(checksums, current, args.version)
        checksums = release_checksums(args.version, names, fetch)
    except (ValueError, URLError) as e:
        sys.exit(f"error: {e}")

    setup_py.write_text(updated_setup_py(source, args.version, checksums))
    for name, checksum in checksums.items():
        print(f"{checksum}  {name}")
    print(f"Pinned libddwaf {args.version} in {setup_py}")


if __name__ == "__main__":
    main()
