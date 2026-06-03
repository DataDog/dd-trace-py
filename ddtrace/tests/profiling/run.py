import runpy
import sys


module = sys.argv[1]
del sys.argv[0]
runpy.run_module(module, run_name="__main__")
