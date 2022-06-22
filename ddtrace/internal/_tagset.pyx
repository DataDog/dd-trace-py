"""
Tagset eBNF::

    tagset = tag, { ",", tag };
    tag = key, "=", value;
    key = { ? ASCII 32-126 ? - equal or comma or space };
    value = { ? ASCII 32-126 ? - comma };
    equal or comma = "=" | ",";
    space = " ";
"""

class TagsetEncodeError(ValueError):
    pass


class TagsetMaxSizeEncodeError(TagsetEncodeError):
    """Exception used when the encoded values exceed the max allowed size

    This exception will contain the:

    - original/in-tact values that are being encoded
    - the max size configured
    - the valid/encoded tagset up until the max size would be exceeded

    Example::

        try:
            tagset = encode_tagset_values({"a": "1", "b": 2, "c": "3"}, max_size=6)
        except TagsetMaxSizeEncodeError as e:
            assert e.values = {"a": "1", "b": "2", "c": "3"}
            assert e.max_size = 6
            assert e.current_results = "a=1,b=2"
    """
    def __init__(self, values, max_size, current_results):
        # type: (Dict[str, str], int, str) -> None
        self.values = values
        self.max_size = max_size
        self.current_results = current_results

        msg = "Tagset cannot encode {!r} would exceed max size of {!r}".format(values, max_size)
        super(TagsetMaxSizeEncodeError, self).__init__(msg)


class TagsetDecodeError(ValueError):
    pass


class TagsetMaxSizeDecodeError(TagsetDecodeError):
    """Exception used when tagset compatible string is greater than the max size

    This exception will contain the:

    - tagset string being decoded
    - the max size configured

    Example::

        try:
            tagset = decode_tagset_values("key=value", max_size=8)
        except TagsetMaxSizeDecodeError as e:
            assert e.tagset = "key=value"
            assert e.max_size = 8
    """
    def __init__(self, tagset, max_size):
        # type: (Dict[str, str], int, str) -> None
        self.tagset = tagset
        self.max_size = max_size

        msg = "Tagset cannot decode {!r} which exceeds max size of {!r} with length {!r}".format(tagset, max_size, len(tagset))
        super(TagsetMaxSizeDecodeError, self).__init__(msg)


cdef inline int is_equal(int c):
    # '='
    return c == 61


cdef inline int is_comma(int c):
    # ','
    return c == 44


cdef inline int is_valid_key_char(int c):
    # string.printable - " ,="
    # 32 = " "
    # 44 = ",""
    # 61 = "="
    return (33 <= c <= 43) or (45 <= c <= 60) or (62 <= c <= 126)


# Same as is_valid_key_char except spaces are allowed
cdef inline int is_valid_value_char(int c):
    # string.printable - ","
    # 44 = ",""
    return c == 32 or is_equal(c) or is_valid_key_char(c)


cpdef dict decode_tagset_string(str tagset, int max_size=512):
    # type: (str, int) -> Dict[str, str]
    """Parse a tagset compatible string into a dictionary of tag key/values

    Examples::

        >>> decode_tagset_string("key=value")
        {"key": "value"}
        >>> decode_tagset_string("a=1,b=2,c=3")
        {"a": "1", "b": "2", "c": "3"}

    :param str tagset: String of encoded "key=value" pairs to decode into a dictionary
    :param int max_size: The max size allowed for length of the incoming string (default: 512)
    :rtype: dict
    :returns: a Dict[str,str] of decoded key/value pairs from the provided string
    :raises TagsetDecodeError: When the provided format is not valid
    """
    cdef dict res = {}
    cdef str c
    cdef int o
    cdef str cur_val = ""
    cdef str cur_key = ""
    cdef int is_parsing_key = 1

    # No tagset provided, short circuit the response
    if not tagset:
        return res

    # Raise an exception that the incoming tagset string exceeds the max size
    if len(tagset) > max_size:
        raise TagsetMaxSizeDecodeError(tagset, max_size)

    # DEV: Parse in a single pass of `tagset`
    #      `is_parsing_key` is used to know if we are on
    #      right or left side of an `=`
    for c in tagset:
        o = ord(c)
        if is_parsing_key:
            if is_equal(o):
                if not cur_key:
                    raise TagsetDecodeError("Empty keys are not allowed: {!r}".format(tagset))
                is_parsing_key = 0
                continue

            if not is_valid_key_char(o):
                raise TagsetDecodeError("Unexpected {!r} character for key {}: {!r}".format(c, cur_key, tagset))
            cur_key += c
        else:
            if is_comma(o):
                cur_val = cur_val.strip(" ")
                if not cur_val:
                    raise TagsetDecodeError("Empty values are not allowed: {!r}".format(tagset))
                res[cur_key] = cur_val
                cur_key, cur_val, is_parsing_key = "", "", 1
                continue

            if not is_valid_value_char(o):
                raise TagsetDecodeError("Unexpected character {!r} for value {}={}: {!r}".format(c, cur_key, cur_val, tagset))
            cur_val += c

    # Reached EOF, do we have a key/value pair we need to save?
    if cur_key:
        cur_val = cur_val.strip(" ")
        if cur_val:
            res[cur_key] = cur_val
        else:
            raise TagsetDecodeError("Expected value for key {!r} instead got EOF: {!r}".format(cur_key, tagset))

    return res

cdef bint _key_is_valid(str key):
    """Helper to ensure a key's characters are all valid"""
    if not key:
        return 0

    for c in key:
        if not is_valid_key_char(ord(c)):
            return 0
    return 1


cdef bint _value_is_valid(str value):
    """Helper to ensure a values's characters are all valid"""
    if not value:
        return 0

    for c in value:
        if not is_valid_value_char(ord(c)):
            return 0
    return 1


cpdef str encode_tagset_values(object values, int max_size=512):
    # type: (Dict[str, str], int) -> str
    """Convert a dictionary of tag key/values into a tagset compatible string

    Example::

        >>> encode_tagset_values({"key": "value"})
        "key=value"
        >>> encode_tagset_values({"a": "1", "b": "2", "c": "3"})
        "a=1,b=2,c=3"

    :param dict values: Dict[str,str] of key/value pairs to encode
    :param int max_size: The max size to allow the resulting string to be (default: 512)
    :rtype: str
    :returns: tagset encoded key/value pairs
    :raises TagsetMaxSizeEncodeError: Raised when we will exceed the provided max size
    :raises TagsetEncodeError: Raised when we encounter an exception character in a key or value
    """
    cdef str res = ""
    cdef str key
    cdef str value
    cdef int i

    for i, (key, value) in enumerate(values.items()):
        # Strip any leading/trailing spaces
        key = key.strip(" ")
        value = value.strip(" ")

        if not _key_is_valid(key):
            raise TagsetEncodeError("Key is not valid: {!r}".format(key))
        if not _value_is_valid(value):
            raise TagsetEncodeError("Value is not valid: {!r}".format(value))

        encoded = "{}={}".format(key, value)
        # Prefix every item except the first with `,` for separator
        if i > 0:
            encoded = "," + encoded

        # Raise an exception that we will exceed the max size
        # The exception has the value up until now if the caller
        # wants to use the partially encoded value
        if len(res) + len(encoded) > max_size:
            raise TagsetMaxSizeEncodeError(values, max_size, res)

        res += encoded
    return res
