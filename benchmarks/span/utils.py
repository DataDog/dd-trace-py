import random
import string


def gen_tags(scenario):
    tag_values = [rands(size=scenario.ltags) for _ in range(scenario.ntags)]
    tag_keys = [rands(size=scenario.ltags) for _ in range(scenario.ntags)]
    tags = {tag_keys[i]: tag_values[i] for i in range(len(tag_keys))}
    return tags


def gen_metrics(scenario):
    metric_keys = [rands(size=16) for _ in range(scenario.nmetrics)]
    metric_values = [random.randint(0, 2 ** 16) for _ in range(scenario.nmetrics)]
    tags = {metric_keys[i]: metric_values[i] for i in range(len(metric_keys))}
    return tags


def random_w_n_digits(lmetrics):
    range_start = 10 ** (lmetrics - 1)
    range_end = (10 ** lmetrics) - 1
    return random.randint(range_start, range_end)


def rands(size=6, chars=string.ascii_uppercase + string.digits):
    return "".join(random.choice(chars) for _ in range(size))
