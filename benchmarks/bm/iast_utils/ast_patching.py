#!/usr/bin/env python3

import os
import shutil


PROJECT_NAME = "my_project"
NUM_MODULES = 300  # Number of modules to create


# Template for a Python module generator
def module_template(module_number, import_modules):
    imports_block = ""
    if import_modules:
        imports_block = "\n".join([f"from {import_module} import *" for import_module in import_modules])

    body_block = f"""
import os

def func_{module_number}():
    print('This is function func_{module_number} from module_{module_number}.py')

class Class{module_number}:
    def __init__(self):
        print('This is Class{module_number} from module_{module_number}.py')

    def slice_test(self, arg):
        return arg[1:3]

    def index_access(self, arg):
        return arg[1]

    def os_path_join(self, arg1, arg2):
        return os.path.join(arg1, arg2)

    def string_concat(self, arg1, arg2):
        return arg1 + arg2

    def string_fstring(self, arg1, arg2):
        return f'{{arg1}} {{arg2}}'

    def string_format(self, arg1, arg2):
        return '{{0}} {{1}}'.format(arg1, arg2)

    def string_format_modulo(self, arg1, arg2):
        return '%s %s' % (arg1, arg2)

    def string_join(self, arg1, arg2):
        return ''.join([arg1, arg2])

    def string_decode(self, arg):
        return arg.decode()

    def string_encode(self, arg):
        return arg.encode("utf-8")

    def string_replace(self, arg, old, new):
        return arg.replace(old, new)

    def bytearray_extend(self, arg1, arg2):
        return arg1.extend(arg2)

    def string_upper(self, arg):
        return arg.upper()

    def string_lower(self, arg):
        return arg.lower()

    def string_swapcase(self, arg):
        return arg.swapcase()

    def string_title(self, arg):
        return arg.title()

    def string_capitalize(self, arg):
        return arg.capitalize()

    def string_casefold(self, arg):
        return arg.casefold()

    def string_translate(self, arg, table):
        return arg.translate(table)

    def string_zfill(self, arg, width):
        return arg.zfill(width)

    def string_ljust(self, arg, width):
        return arg.ljust(width)

    def str_call(self, arg):
        return str(arg)

    def bytes_call(self, arg):
        return bytes(arg)

    def bytearray_call(self, arg):
        return bytearray(arg)

if __name__ == "__main__":
    print('This is module_{module_number}.py')
"""
    return f"{imports_block}\n{body_block}"


def create_project_structure():
    project_name = PROJECT_NAME
    num_modules = NUM_MODULES

    # Create the project directory
    os.makedirs(project_name, exist_ok=True)

    # Create the __init__.py file to make the directory a package
    with open(os.path.join(project_name, "__init__.py"), "w") as f:
        f.write(f"# This is the __init__.py file for the {project_name} package\n")

    # last file path
    module_path = ""
    # Create the modules
    for i in range(1, num_modules + 1):
        module_name = f"module_{i}.py"
        module_path = os.path.join(project_name, module_name)

        # Import all the previous modules in the last module only
        if i == num_modules:
            import_modules = [f"module_{j}" for j in range(1, i)]
        else:
            import_modules = None

        # Render the template with context
        rendered_content = module_template(i, import_modules)

        with open(module_path, "w") as f:
            f.write(rendered_content)

    return module_path


def destroy_project_structure():
    project_name = PROJECT_NAME
    # Remove the project directory
    shutil.rmtree(project_name)
