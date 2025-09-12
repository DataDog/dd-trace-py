from psycopg2.extensions import adapt


def adapt_list(obj_list):
    value = adapt(obj_list)
    return b"r-" + value.getquoted()
