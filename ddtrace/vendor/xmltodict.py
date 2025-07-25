"""This module was extracted from the xmltodict package, version 0.14.2."""

from typing import Union
from xml.parsers import expat


__author__ = "Martin Blech"
__version__ = "0.14.2"
__license__ = "MIT"


class ParsingInterrupted(Exception):
    pass


class _DictSAXHandler:
    def __init__(
        self
    ):
        self.path = []
        self.stack = []
        self.data = []
        self.item = None
        self.attr_prefix = "@"
        self.cdata_key = "#text"
        self.cdata_separator = ""
        self.namespace_separator = ":"
        self.namespace_declarations = {}
        self.comment_key = "#comment"

    def startNamespaceDecl(self, prefix, uri):
        self.namespace_declarations[prefix or ""] = uri

    def startElement(self, full_name, attrs):
        if not isinstance(attrs, dict):
            attrs = dict(zip(attrs[0::2], attrs[1::2]))
        if attrs and self.namespace_declarations:
            attrs["xmlns"] = self.namespace_declarations
            self.namespace_declarations = {}
        self.path.append((full_name, attrs or None))
        self.stack.append((self.item, self.data))
        attr_entries = []
        for key, value in attrs.items():
            key = self.attr_prefix + key
            attr_entries.append((key, value))
        attrs = dict(attr_entries)
        self.item = attrs or None
        self.data = []

    def endElement(self, full_name):
        if len(self.path) == 0:
            item = self.item
            if item is None:
                item = None if not self.data else self.cdata_separator.join(self.data)

        if self.stack:
            data = None if not self.data else self.cdata_separator.join(self.data)
            item = self.item
            self.item, self.data = self.stack.pop()
            if data:
                data = data.strip() or None
            if item is not None:
                if data:
                    self.push_data(item, self.cdata_key, data)
                self.item = self.push_data(self.item, full_name, item)
            else:
                self.item = self.push_data(self.item, full_name, data)
        else:
            self.item = None
            self.data = []
        self.path.pop()

    def characters(self, data):
        if not self.data:
            self.data = [data]
        else:
            self.data.append(data)

    def push_data(self, item, key, data):
        if item is None:
            item = {}
        try:
            value = item[key]
            if isinstance(value, list):
                value.append(data)
            else:
                item[key] = [value, data]
        except KeyError:
                item[key] = data
        return item



def parse(xml_input: Union[bytes, str]):
    """Parse the given XML input and convert it into a dictionary."""
    handler = _DictSAXHandler()
    encoding = None
    if isinstance(xml_input, str):
        encoding = "utf-8"
        xml_input = xml_input.encode(encoding)
    parser = expat.ParserCreate(encoding, None)
    try:
        parser.ordered_attributes = True
    except AttributeError:
        # Jython's expat does not support ordered_attributes
        pass
    parser.StartNamespaceDeclHandler = handler.startNamespaceDecl
    parser.StartElementHandler = handler.startElement
    parser.EndElementHandler = handler.endElement
    parser.CharacterDataHandler = handler.characters
    parser.buffer_text = True
    parser.DefaultHandler = lambda x: None
    # Expects an integer return; zero means failure -> expat.ExpatError.
    parser.ExternalEntityRefHandler = lambda *x: 1
    parser.Parse(xml_input, True)
    return handler.item
