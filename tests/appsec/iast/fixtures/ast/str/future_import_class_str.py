#!/usr/bin/env python3
"""
Some
multi-line
docstring
here
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import html


class my_fixture:
    def __repr__(self):
        return str(self)


html_escape = html.escape
