from werkzeug.utils import safe_join
from werkzeug.utils import secure_filename


def werkzeug_secure_filename(tainted_value):
    return "a-" + secure_filename(tainted_value)


def werkzeug_secure_safe_join(tainted_value):
    base_dir = "/var/www/uploads"
    return safe_join(base_dir, tainted_value)
