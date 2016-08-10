
import ctypes
import logging
import struct

import bson
from bson.codec_options import CodecOptions
from bson.son import SON



# MongoDB wire protocol commands
# http://docs.mongodb.com/manual/reference/mongodb-wire-protocol
OP_CODES = {
    1    : "reply",
    1000 : "msg",
    2001 : "update",
    2002 : "insert",
    2003 : "reserved",
    2004 : "query",
    2005 : "get_more",
    2006 : "delete",
    2007 : "kill_cursors",
    2010 : "command",
    2011 : "command_reply",
}

header_struct = struct.Struct("<iiii")


class Command(object):
    """ Command stores information about a pymongo network command, """

    __slots__ = ['name', 'coll', 'db', 'tags', 'metrics', 'query']

    def __init__(self, name, coll):
        self.name = name
        self.coll = coll
        self.db = None
        self.tags = {}
        self.metrics = {}
        self.query = None

    def __repr__(self):
        return (
            "Command("
            "name=%s,"
            "coll=%s)"
        ) % (self.name, self.coll)


def parse_msg(msg_bytes):
    """ Return a command from a binary mongo db message or None if we shoudln't
        trace it. The protocol is documented here:
        http://docs.mongodb.com/manual/reference/mongodb-wire-protocol
    """
    header = header_struct.unpack_from(msg_bytes, 0)
    (length, req_id, response_to, op_code) = header

    op = OP_CODES.get(op_code)
    if not op:
        log.debug("unknown op code: %s", op_code)
        return None

    db = None
    coll = None

    offset = header_struct.size
    cmd = None
    if op == "query":
        # all of these commands have an int32 that for flags or reserved use.
        # skip that
        offset += 4
        ns = _cstring(msg_bytes[offset:])
        offset += len(ns) + 1  # include null terminator

        # note: here coll could be '$cmd' because it can be overridden in the
        # query itself (like {"insert":"songs"})
        db, coll = _split_namespace(ns)

        offset += 8  # skip num skip & num to return

        # FIXME[matt] this is likely the only performance cost here. could we
        # be processing a massive message? maybe cap the size here?
        spec = bson.decode_iter(
            msg_bytes[offset:],
            codec_options=CodecOptions(SON),
        ).next()
        cmd = parse_spec(spec)
        cmd.db = db

    return cmd

def parse_query(query):
    """ Return a command parsed from the given mongo db query. """
    db, coll = None, None
    ns = getattr(query, "ns", None)
    if ns:
        # version < 3.1 stores the full namespace
        db, coll = _split_namespace(ns)
    else:
        # version >= 3.1 stores the db and coll seperately
        coll = getattr(query, "coll", None)
        db = getattr(query, "db", None)

    # FIXME[matt] mongo < 3.1 _Query doesn't not have a name field,
    # so hardcode to query.
    cmd = Command("query", coll)
    cmd.query = query.spec
    cmd.db = db
    return cmd

def parse_spec(spec):
    """ Return a Command that has parsed the relevant detail for the given
        pymongo SON spec.
    """

    # the first element is the command and collection
    items = list(spec.items())
    if not items:
        return None
    name, coll = items[0]
    cmd = Command(name, coll)

    if 'ordered' in spec: # in insert and update
        cmd.tags['mongodb.ordered'] = spec['ordered']

    if cmd.name == 'insert':
        if 'documents' in spec:
            cmd.metrics['mongodb.documents'] = len(spec['documents'])

    elif cmd.name == 'update':
        updates = spec.get('updates')
        if updates:
            # FIXME[matt] is there ever more than one here?
            cmd.query = updates[0].get("q")

    elif cmd.name == 'delete':
        dels = spec.get('deletes')
        if dels:
            # FIXME[matt] is there ever more than one here?
            cmd.query = dels[0].get("q")

    return cmd

def _cstring(raw):
    """ Return the first null terminated cstring from the bufffer. """
    return ctypes.create_string_buffer(raw).value

def _split_namespace(ns):
    """ Return a tuple of (db, collecton) from the "db.coll" string. """
    if ns:
        return ns.decode("utf-8").split(".")
    return (None, None)
