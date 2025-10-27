"""Layer 2 - Imported dynamically, has its own imports"""

# Top-level import even though this module itself is imported dynamically
from tests.coverage.included_path.layer3_toplevel import layer3_toplevel_function


def layer2_dynamic_function(b):
    # Use top-level import
    step1 = layer3_toplevel_function(b)

    # Dynamic imports - both function and constants
    from tests.coverage.included_path.constants_dynamic import OFFSET
    from tests.coverage.included_path.layer3_dynamic import layer3_dynamic_function

    step2 = layer3_dynamic_function(step1)
    return step2 + OFFSET - 5
