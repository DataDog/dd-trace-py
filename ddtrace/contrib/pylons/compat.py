try:
    from pylons.templating import render_mako  # noqa:F401

    # Pylons > 0.9.7
    legacy_pylons = False
except ImportError:
    # Pylons <= 0.9.7
    legacy_pylons = True
