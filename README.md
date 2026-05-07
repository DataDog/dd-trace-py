# ddtrace (meta-repository)

Parent checkout that composes Datadog Python tracing and internal packages as **git submodules**.

## Layout

| Path               | Submodule    | Description                                      |
|--------------------|-------------|--------------------------------------------------|
| `dd-trace-py/`     | dd-trace-py | Public APM client ([dd-trace-py](https://github.com/DataDog/dd-trace-py)) |
| `ddtrace-internal/`| ddtrace-internal | Internal stub package (`ddtrace-internal` distribution) |

## First-time setup

```bash
git clone <this-repo-url> ddtrace
cd ddtrace
git submodule update --init --recursive
```

If you are migrating from a standalone `dd-trace-py` clone, point `dd-trace-py` at your existing remote; same for `ddtrace-internal` after its remote exists.

### Local submodule URL (before `ddtrace-internal` exists on GitHub)

From `ddtrace/`:

```bash
git submodule add ../ddtrace-internal ddtrace-internal
git submodule add ../dd-trace-py dd-trace-py
```

Adjust relative paths if your clones live elsewhere. Then commit `.gitmodules` and the submodule gitlinks.

## Build

From this directory:

```bash
make ddtrace-internal   # editable install of the internal package
```

For `dd-trace-py`, follow that repository’s documentation (Riot, Hatch, etc.).
