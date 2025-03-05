def module_func():
    value = ""
    try:
        raise ValueError("<error_function_module>")
    except Exception:
        value += "<except_module>"
    return value


class AClass:
    def instance_method(self):
        value = ""
        try:
            raise ValueError("<error_method>")
        except Exception:
            value += "<except_method>"
        return value

    @staticmethod
    def static_method():
        value = ""
        try:
            raise ValueError("<error_static>")
        except Exception:
            value += "<except_static>"
        return value
