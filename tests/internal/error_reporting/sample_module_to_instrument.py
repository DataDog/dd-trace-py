def a_function_at_the_root_level():
    value = ""
    try:
        value += "<try_root>"
        raise ValueError("<error_function_root>")
    except Exception:
        value += "<except_root>"
    return value


class AClass:
    def instance_method(self):
        value = ""
        try:
            value += "<try_method>"
            raise ValueError("<error_method>")
        except Exception:
            value += "<except_method>"
        return value

    @staticmethod
    def static_method():
        value = ""
        try:
            value += "<try_static>"
            raise ValueError("<error_static>")
        except Exception:
            value += "<except_static>"
        return value
