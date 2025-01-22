_object_span_links = {}


def get_object_id(obj):
    return f"{type(obj).__name__}_{id(obj)}"


def add_span_links_to_object(obj, span_links):
    obj_id = get_object_id(obj)
    if obj_id not in _object_span_links:
        _object_span_links[obj_id] = []
    _object_span_links[obj_id] += span_links


def get_span_links_from_object(obj):
    return _object_span_links.get(get_object_id(obj), [])
