import types


code1 = """
def evaluate_locale():
    from babel import Locale
    response = Locale('en', 'US').currency_formats['standard']
    return response
"""


def babel_locale():
    module_name = "test_babel"
    compiled_code = compile(code1, "tests/appsec/integrations/packages_tests/", mode="exec")
    module_changed = types.ModuleType(module_name)
    exec(compiled_code, module_changed.__dict__)  # define evaluate
    result = eval("evaluate_locale()", module_changed.__dict__)  # llama evaluate
    return f"OK:{result}"


def babel_to_python(n):
    def evaluate_to_python(n):
        from babel.plural import to_python

        func = to_python({"one": "n is 1", "few": "n in 2..4"})
        return func(n)

    return f"OK:{evaluate_to_python(n)}"
