class ShellGlobals(object):
    def __setattr__(self, name, value):
        self.__dict__[name] = value

    def __delattr__(self, name):
        del self.__dict__[name]

    def __getattr__(self, name):
        # backwards compatibility
        if name == "mysql":
            return "mysql"
        elif name == "mysqlx":
            return "mysqlx"
        return self.__dict__[name]


globals = ShellGlobals()  # noqa: A001

del ShellGlobals
