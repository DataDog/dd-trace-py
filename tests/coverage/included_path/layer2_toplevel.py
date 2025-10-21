"""Layer 2 - Has top-level import and dynamic import"""

# Top-level import
from tests.coverage.included_path.layer3_toplevel import layer3_toplevel_function


def layer2_toplevel_function(a):
    # Use the top-level imported function
    intermediate = layer3_toplevel_function(a)
    
    # Dynamic import inside function
    from tests.coverage.included_path.layer3_dynamic import layer3_dynamic_function
    
    final = layer3_dynamic_function(intermediate)
    return final

