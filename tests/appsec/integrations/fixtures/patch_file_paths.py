from werkzeug.utils import secure_filename


def werkzeug_secure_filename(tainted_value):
    return "a-" + secure_filename(tainted_value)
