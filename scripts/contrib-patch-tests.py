# import importlib.util
import ast
from pathlib import Path
from textwrap import dedent


CONTRIB_PATH = Path.cwd() / "ddtrace" / "contrib"
CONTRIB_TEST_PATH = Path().cwd() / "tests" / "contrib"


def write_patch_tests(contrib):
    print(f"Generating patch test for {contrib}")

    module_ast = ast.parse((CONTRIB_PATH / contrib / "__init__.py").read_text())
    required_modules_node = [
        _ for _ in module_ast.body if isinstance(_, ast.Assign) and _.targets[0].id == "required_modules"
    ]
    if not required_modules_node:
        return

    required_modules = [_.value for _ in required_modules_node[0].value.elts]

    if not required_modules:
        return

    module = required_modules[0]

    (CONTRIB_TEST_PATH / contrib / f"test_{contrib}_patch.py").write_text(
        dedent(
            f"""
            from ddtrace.contrib.{contrib} import patch
            # from ddtrace.contrib.{contrib} import unpatch
            from tests.contrib.patch import PatchTestCase


            class Test{contrib.title()}Patch(PatchTestCase.Base):
                __integration_name__ = "{contrib}"
                __module_name__ = "{module}"
                __patch_func__ = patch
                # __unpatch_func__ = unpatch

                def assert_module_patched(self, {module.replace(".", "_")}):
                    pass

                def assert_not_module_patched(self, {module.replace(".", "_")}):
                    pass

                def assert_not_module_double_patched(self, {module.replace(".", "_")}):
                    pass
                    
            """
        ).lstrip()
    )


# Take all the contribs with a patch submodule
contribs = [p.parent.name for p in CONTRIB_PATH.glob("*/patch.py")]


for c in contribs:
    if not any((CONTRIB_TEST_PATH / c / f).is_file() for f in (f"test_patch.py", f"test_{c}_patch.py")):
        write_patch_tests(c)
