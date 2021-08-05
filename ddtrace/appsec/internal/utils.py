from collections import OrderedDict
from collections import defaultdict
from itertools import chain
import json

import yaml


_EVENT_RULES_MANIFEST_0_1 = {
    "http.method": OrderedDict(
        inherit_from="method",
        run_on_key=False,
        run_on_value=True,
    ),
    "http.target": OrderedDict(
        inherit_from="target",
        run_on_key=False,
        run_on_value=True,
    ),
    "http.headers": OrderedDict(
        inherit_from="headers",
        run_on_key=False,
        run_on_value=True,
    ),
    "http.user_agent": OrderedDict(
        inherit_from="headers",
        key_access=OrderedDict(paths=[["user-agent"], ["User-Agent"]], is_allowlist=True),
        run_on_key=False,
        run_on_value=True,
    ),
    "http.server.query": OrderedDict(
        inherit_from="query",
        run_on_key=False,
        run_on_value=True,
    ),
    "http.server.cookies": OrderedDict(
        inherit_from="cookies",
        run_on_key=False,
        run_on_value=True,
    ),
    "http.server.body": OrderedDict(
        inherit_from="body",
        run_on_key=False,
        run_on_value=True,
    ),
    "http.server.path_params": OrderedDict(
        inherit_from="path_params",
        run_on_key=False,
        run_on_value=True,
    ),
}


def match_regex_operation(condition):
    operation = OrderedDict(
        operator="@rx",
        targets=condition["parameters"]["inputs"],
        value=condition["parameters"]["regex"],
    )
    options = condition["parameters"].get("options")
    if options is not None:
        operation["options"] = OrderedDict(options)
    return operation


def phrase_match_operation(condition):
    operation = OrderedDict(
        operator="@pm",
        targets=condition["parameters"]["inputs"],
        value=condition["parameters"]["list"],
    )
    options = condition["parameters"].get("options")
    if options is not None:
        operation["options"] = OrderedDict(options)
    return operation


_EVENT_RULES_OPERATIONS_0_1 = {
    "match_regex": match_regex_operation,
    "phrase_match": phrase_match_operation,
}


def update_manifest(event_id, manifest, sqreen_filter):
    for original_target in sqreen_filter["targets"]:
        parts = original_target.split(":", 1)
        if len(parts) == 2:
            target, path = parts
        else:
            target, path = original_target, None

        entry = manifest.get(target)
        if entry is None:
            raise NotImplementedError("event rule {!r} target {!r} is not supported".format(event_id, target))

        if path is not None:
            manifest[original_target] = OrderedDict(
                inherit_from=entry["inherit_from"],
                key_access=OrderedDict(paths=[[path]], is_allowlist=True),
                run_on_key=entry["run_on_key"],
                run_on_value=entry["run_on_value"],
            )

    return manifest


def get_ruleset_id(event):
    action = event.get("action", "record")
    typ = event.get("tags", {}).get("type", "global")
    return "-".join((typ, action))


def event_rules_to_sqreen(event_rules):
    """Convert event rules to Sqreen rules."""

    doc = yaml.safe_load(event_rules)
    version = doc.get("version", "0")
    if version not in ("0.1",):
        raise NotImplementedError("Unsupported event rules version {!r}".format(version))

    manifest = OrderedDict(_EVENT_RULES_MANIFEST_0_1)
    ruleset = defaultdict(list)

    for event in doc.get("events", []):
        event_id = str(event["id"])
        filters = []

        for cond in event.get("conditions", []):
            operation = cond.get("operation")
            op = _EVENT_RULES_OPERATIONS_0_1.get(operation)
            if op is None:
                raise NotImplementedError("event rule {!r} operation {!r} is not supported".format(event_id, operation))
            sqreen_filter = op(cond)
            manifest = update_manifest(event_id, manifest, sqreen_filter)
            sqreen_filter["transformations"] = event.get("transformers", [])
            filters.append(sqreen_filter)

        if not filters:
            raise ValueError("event rule {!r} has no condition".format(event_id))

        ruleset[get_ruleset_id(event)].append(
            OrderedDict(
                rule_id=event_id,
                filters=filters,
            )
        )

    sqreen_rules = OrderedDict(
        manifest=manifest,
        rules=list(chain.from_iterable(ruleset.values())),
        flows=[
            OrderedDict(
                name=name,
                steps=[OrderedDict(id="start", rule_ids=[r["rule_id"] for r in rules], on_match="exit_monitor")],
            )
            for name, rules in ruleset.items()
        ],
    )
    return json.dumps(sqreen_rules)
