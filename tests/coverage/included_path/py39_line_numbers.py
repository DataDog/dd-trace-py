x = {}

def g(*args, **kwargs):
    pass


def precise_line_numbers():
    g(None,
      **x)


def imprecise_line_numbers():
    g(
        None, **x)


def call_all_functions():
    precise_line_numbers()
    imprecise_line_numbers()
