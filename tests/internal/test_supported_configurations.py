import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize("args", [(), ("--check",)], ids=["generate", "check"])
@pytest.mark.parametrize(
    "read,extra_binding,diagnostic",
    [
        ('startup.read("_missing")', {}, "Missing startup configuration bindings: _missing"),
        ('startup.read(property_name="_missing")', {}, "Missing startup configuration bindings: _missing"),
        (
            'property_name = "_missing"\nstartup.read(property_name=property_name)',
            {},
            "Startup configuration read requires a string literal",
        ),
        (
            'startup.read("service")',
            {"internalPropertyName": "service", "type": "string", "default": None},
            "Startup configuration property service is bound more than once",
        ),
        (
            'startup.read("service")',
            {"internalPropertyName": "env", "type": "object", "default": None},
            "Unsupported startup configuration type object for DD_ENV",
        ),
    ],
    ids=["missing-positional", "missing-keyword", "dynamic-property", "duplicate-binding", "unsupported-type"],
)
def test_startup_registry_rejects_invalid_bindings(tmp_path, args, read, extra_binding, diagnostic):
    script = tmp_path / "scripts" / "supported_configurations.py"
    script.parent.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[2] / "scripts" / script.name, script)
    settings = tmp_path / "ddtrace" / "internal" / "settings"
    settings.mkdir(parents=True)
    (settings / "_config.py").write_text(
        f"from ddtrace.internal.telemetry import StartupConfig as Resolver\nstartup = Resolver()\n{read}\n",
        encoding="utf-8",
    )
    bindings = {"DD_SERVICE": [{"internalPropertyName": "service", "type": "string", "default": None}]}
    if extra_binding:
        bindings["DD_ENV"] = [extra_binding]
    (tmp_path / "supported-configurations.json").write_text(
        json.dumps({"version": "2", "supportedConfigurations": bindings}),
        encoding="utf-8",
    )
    generated = settings / "_supported_configurations.py"
    generated.write_text("# Original registry\n", encoding="utf-8")

    result = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, check=False)

    assert result.returncode == 1
    assert diagnostic in result.stderr
    assert generated.read_text(encoding="utf-8") == "# Original registry\n"
