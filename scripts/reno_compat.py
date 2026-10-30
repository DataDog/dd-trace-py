#!/usr/bin/env python3
"""Run Reno with same-path add/modify merge entries normalized.

Reno 4.1's aggregator rejects a note added relative to one merge parent and
modified relative to the other. Treat that path as an addition, retaining its
merge-commit contents. Other combinations still go through Reno's validation.
"""


def normalize_merge_changes(entry, changes):
    flattened = [change for group in changes for change in (group if isinstance(group, list) else [group])]
    if len(entry.commit.parents) < 2:
        return flattened
    added = {change.new.path for change in flattened if change.type == "add"}
    return [change for change in flattened if not (change.type == "modify" and change.new.path in added)]


def main():
    from reno import main as reno_main
    from reno import scanner

    aggregate = scanner._ChangeAggregator.aggregate_changes

    def aggregate_merge(self, entry, changes):
        return aggregate(self, entry, normalize_merge_changes(entry, changes))

    scanner._ChangeAggregator.aggregate_changes = aggregate_merge
    return reno_main.main()


if __name__ == "__main__":
    raise SystemExit(main())
