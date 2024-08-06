from benchmarks import bm
from benchmarks.appsec_iast_aspects import functions

# from pprint import pprint
# pprint(functions)


# class IAST_Aspects(bm.Scenario):
#     iast_enabled: bool
#
#     def run(self):
#         def _(loops):
#             for _ in range(loops):
#                 for fkey, fvalue in functions.items():
#                     if self.iast_enabled:
#                         print(fkey + " patched")
#                         fvalue['patched'](*fvalue['args'])
#                     else:
#                         print(fkey + " unpatched")
#                         fvalue['unpatched'](*fvalue['args'])
#         yield _

class IAST_Aspects_do_capitalize_patched(bm.Scenario):
    iast_enabled: bool

    def run(self):
        self.iast_enabled = False
        def _(loops):
            for _ in range(loops):
                fvalue = functions['do_capitalize']
                if self.iast_enabled:
                    print("iast_enabled")
                    fvalue['patched'](*fvalue['args'])
                else:
                    print("iast_disabled")
                    fvalue['unpatched'](*fvalue['args'])
        yield _
