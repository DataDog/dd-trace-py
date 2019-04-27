from __future__ import print_function

import sys
from nose.tools import ok_


if __name__ == '__main__':
    # detect if `-S` is used
    suppress = len(sys.argv) == 2 and sys.argv[1] is '-S'
    if suppress:
        ok_('sitecustomize' not in sys.modules)
    else:
        ok_('sitecustomize' in sys.modules)

    # ensure the right `sitecustomize` will be imported
    import sitecustomize
    ok_(sitecustomize.CORRECT_IMPORT)
    print('Test success')
