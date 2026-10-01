"""Build and atomically install only the ddtrace native extension.

Other tracer extensions and distribution metadata must already be installed.
Run this script with the interpreter that will run the tracer.
"""

import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--link-mode", choices=("static", "source", "system"), default=os.environ.get("DD_WAF_LINK_MODE", "static")
    )
    parser.add_argument(
        "--offline", action="store_true", help="use Cargo offline mode; libddwaf-sys may still download native archives"
    )
    parser.add_argument(
        "--features",
        default="stats,ffe" if platform.system() == "Windows" else "stats,ffe,profiling,crashtracker",
        help="additional ddtrace features (default: stats,ffe; also profiling,crashtracker on Unix)",
    )
    args = parser.parse_args()
    if platform.system() not in ("Darwin", "Linux", "Windows") or (
        platform.system() != "Windows" and sys.maxsize <= 2**32
    ):
        parser.error("the native WAF build targets Windows and 64-bit macOS/Linux")
    environment = os.environ.copy()
    environment["PYO3_PYTHON"] = sys.executable
    if sys.version_info >= (3, 15):
        environment.setdefault("PYO3_USE_ABI3_FORWARD_COMPATIBILITY", "1")
    target = Path(environment.get("CARGO_TARGET_DIR", str(ROOT / ".cache/native-waf"))).resolve()
    environment["CARGO_TARGET_DIR"] = str(target)
    if args.link_mode == "source":
        environment.pop("LIBDDWAF_PREFIX", None)
    elif args.link_mode == "system" and "LIBDDWAF_PREFIX" not in environment:
        parser.error("system linking requires LIBDDWAF_PREFIX")
    feature = {"static": "waf", "source": "waf-source", "system": "waf-system"}[args.link_mode]
    command = [
        "cargo",
        "build",
        "--manifest-path",
        str(ROOT / "src/native/Cargo.toml"),
        "--release",
        "--locked",
        "--features",
        ",".join(filter(None, (feature, args.features))),
    ]
    if args.offline:
        command.append("--offline")
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    build_target = environment.get("CARGO_BUILD_TARGET")
    artifact_dir = target / build_target if build_target else target
    artifact = (
        artifact_dir
        / "release"
        / {"Darwin": "lib_native.dylib", "Linux": "lib_native.so", "Windows": "_native.dll"}[platform.system()]
    )
    destination = ROOT / "ddtrace/internal/native" / ("_native" + sysconfig.get_config_var("EXT_SUFFIX"))
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(artifact, temporary)
    # Replacing the inode also avoids stale Mach-O code-signature caches.
    temporary.replace(destination)
    print(destination)


if __name__ == "__main__":
    main()
