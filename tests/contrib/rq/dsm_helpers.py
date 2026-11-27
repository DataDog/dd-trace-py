import json


def install_capture(path):
    from ddtrace.internal.datastreams import data_streams_processor

    processor = data_streams_processor()

    def periodic():
        with processor._lock:
            rows = [
                {"tags": tags, "hash": hash_value, "parent_hash": parent_hash, "count": stats.edge_latency.count}
                for bucket in processor._buckets.values()
                for (tags, hash_value, parent_hash), stats in bucket.pathway_stats.items()
            ]
            processor._buckets.clear()
        with open(path, "a") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    processor.periodic = periodic
    return processor


def read_stats(path):
    stats = {}
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return stats
    for line in lines:
        row = json.loads(line)
        key = (row["tags"], row["hash"], row["parent_hash"])
        stats[key] = stats.get(key, 0) + row["count"]
    return stats


def entries(stats, tags):
    return [(hash_value, parent_hash, count) for (t, hash_value, parent_hash), count in stats.items() if t == tags]


def out_tags(queue):
    return "direction:out,topic:%s,type:redis" % queue


def in_tags(queue):
    return "direction:in,topic:%s,type:redis" % queue
