# FFE Fixture Snapshot

These files are copied from the canonical FFE fixture repository.

Canonical source: https://github.com/DataDog/ffe-system-test-data
Source commit: e8777633d09f0e42b082f5ed64d42edeed080220

Do not edit these fixtures directly in dd-trace-py. Add or update shared FFE behavior
in ffe-system-test-data first, then refresh this snapshot.

The weekly update workflow runs `python scripts/update-ffe-fixtures.py` and opens a
draft test PR only when the allowed fixture contents change.
