# https://mail.python.org/pipermail/tutor/2006-August/048596.html

"""
The function below deletes a module by name from the Python interpreter, the
"paranoid" parameter is a list of variable names to remove from every other
module (supposedly being deleted with the module).  Be VERY careful with the
paranoid param; it could cause problems for your interpreter if your
functions and classes are named the same in different modules.  One common
occurrance of this is "error" for exceptions.  A lot of libraries have one
"catch-all" exception called "error" in the module.  If you also named your
exception "error" and decided to include that in the paranoid list... there
go a lot of other exception objects.
"""
def delete_module(modname, paranoid=None):
    from sys import modules
    try:
        thismod = modules[modname]
    except KeyError:
        raise ValueError(modname)
    these_symbols = dir(thismod)
    if paranoid:
        try:
            paranoid[:]  # sequence support
        except Exception:
            raise ValueError('must supply a finite list for paranoid')
        else:
            these_symbols = paranoid[:]
    del modules[modname]
    for mod in modules.values():
        try:
            delattr(mod, modname)
        except AttributeError:
            pass
        if paranoid:
            for symbol in these_symbols:
                if symbol[:2] == '__':  # ignore special symbols
                    continue
                try:
                    delattr(mod, symbol)
                except AttributeError:
                    pass
