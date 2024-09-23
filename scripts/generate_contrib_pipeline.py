from pathlib import Path
import sys

from ruamel.yaml import YAML


ROOT = Path(__file__).parents[1]
CONFIG_TEMPLATE_FILE = ROOT / ".gitlab" / "tests" / "contrib.yml"
CONFIG_GEN_FILE = ROOT / ".gitlab" / "config.gen.yml"

has_error = False
with YAML(output=CONFIG_GEN_FILE) as yaml:
    config = yaml.load(CONFIG_TEMPLATE_FILE)
    yaml.dump(config)

    sys.exit(has_error)
