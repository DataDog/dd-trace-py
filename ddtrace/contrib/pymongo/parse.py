

class Command(object):
    """ Command stores information about a pymongo network command, """

    __slots__ = ['name', 'coll', 'tags', 'metrics', 'query']

    def __init__(self, name, coll):
        self.name = name
        self.coll = coll
        self.tags = {}
        self.metrics = {}
        self.query = None


def parse_query(query):
    """ Return a command parsed from the given mongo db query. """
    cmd = Command(query.name, query.coll)
    cmd.query = query.spec
    return cmd

def parse_spec(spec):
    """ Return a Command that has parsed the relevant detail for the given
        pymongo SON spec.
    """

    # the first element is the command and collection
    name, coll = spec.iteritems().next()
    cmd = Command(name, coll)

    if 'ordered' in spec: # in insert and update
        cmd.tags['mongodb.ordered'] = spec['ordered']

    if cmd.name == 'insert':
        if 'documents' in spec:
            cmd.metrics['mongodb.documents'] = len(spec['documents'])

    elif cmd.name == 'update':
        updates = cmd.get('updates')
        if updates:
            pass

    elif cmd.name == 'delete':
        dels = spec.get('deletes')
        if dels:
            # FIXME[matt] is there ever more than one here?
            cmd.query = dels[0].get("q")

    return cmd


