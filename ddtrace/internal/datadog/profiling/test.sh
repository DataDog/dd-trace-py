#!/bin/bash
bloaty ./build/dd_wrapper/libdd_wrapper.so -d compileunits -s vm -n 35 | sed 's/\/go.*\///'
