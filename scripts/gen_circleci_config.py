# This script is used to generate the CircleCI dynamic config file in
# .circleci/config.gen.yml.
#
# To add new configuration manipulations that are based on top of the template
# file in .circleci/config.templ.yml, add a function named gen_<name> to this
# file. The function will be called automatically when this script is run.


def gen_required_suites(template: dict) -> None:
    """Generate the list of test suites that need to be run."""
    from needs_testrun import for_each_testrun_needed as fetn

    required_suites = template["requires_tests"]["requires"] = []
    fetn(lambda suite: required_suites.append(suite))

    requires_base_venvs = template["requires_base_venvs"]
    template["workflows"]["test"]["jobs"].extend([{suite: requires_base_venvs} for suite in required_suites])


# -----------------------------------------------------------------------------

# The code below is the boilerplate that makes the script work. There is
# generally no reason to modify it.

from argparse import ArgumentParser  # noqa
import logging  # noqa
from pathlib import Path  # noqa
import sys  # noqa
from time import monotonic_ns as time  # noqa

from ruamel.yaml import YAML  # noqa


logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
LOGGER = logging.getLogger(__name__)

argp = ArgumentParser()
argp.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
args = argp.parse_args()
if args.verbose:
    LOGGER.setLevel(logging.INFO)

ROOT = Path(__file__).parents[1]
CONFIG_TEMPLATE_FILE = ROOT / ".circleci" / "config.templ.yml"
CONFIG_GEN_FILE = ROOT / ".circleci" / "config.gen.yml"

# Make the scripts and tests folders available for importing.
sys.path.append(str(ROOT / "scripts"))
sys.path.append(str(ROOT / "tests"))


with YAML(output=CONFIG_GEN_FILE) as yaml:
    LOGGER.info("Loading configuration template from %s", CONFIG_TEMPLATE_FILE)
    config = yaml.load(CONFIG_TEMPLATE_FILE)

    LOGGER.info("Configuration generation steps:")
    for name, func in dict(globals()).items():
        if name.startswith("gen_"):
            desc = func.__doc__.splitlines()[0]
            try:
                start = time()
                func(config)
                end = time()
                LOGGER.info("- %s: %s [took %dms]", name, desc, int((end - start) / 1e6))
            except Exception as e:
                LOGGER.error("- %s: %s [reason: %s]", name, desc, str(e))

    LOGGER.info("Writing generated configuration to %s", CONFIG_GEN_FILE)
    yaml.dump(config)
