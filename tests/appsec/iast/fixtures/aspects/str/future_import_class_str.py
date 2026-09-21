#!/usr/bin/env python3
"""
Some
multi-line
docstring
here
"""

import html


class my_fixture:
    def __repr__(self):
        return str(self)


html_escape = html.escape
