symbol1 = "foo"
symbol2 = "bar"
symbol3 = symbol1 + symbol2
symbol4 = symbol3[1]

__ddtrace_mark = "baz"


def __dir__():
    return ["custom_added"] + [i for i in globals()]
