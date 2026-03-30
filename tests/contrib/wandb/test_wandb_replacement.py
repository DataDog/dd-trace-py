import ddtrace.contrib.internal.wandb.patch as wandb_patch


def _is_wandb_run_id(value):
    return isinstance(value, str) and len(value) == 8 and all(c.isdigit() or c.islower() for c in value)


class _FakeSpan(object):
    def __init__(self, name):
        self.name = name
        self.tags = {}
        self.exc_info = None
        self.finished = False

    def _set_attribute(self, key, value):
        self.tags[key] = value

    def set_exc_info(self, *exc_info):
        self.exc_info = exc_info

    def finish(self):
        self.finished = True


class _FakeTracer(object):
    def __init__(self):
        self.spans = []

    def start_span(self, name, **kwargs):
        span = _FakeSpan(name)
        span.kwargs = kwargs
        self.spans.append(span)
        return span


class _FakeRun(object):
    def __init__(self):
        self.id = "run-123"
        self.logged = []
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.exited = True
        return None

    def log(self, data, *args, **kwargs):
        self.logged.append((data, args, kwargs))


class _FakeWandb(object):
    __version__ = "0.0.1"

    def __init__(self):
        self.logged_in = False
        self.logged = []
        self.run = _FakeRun()

    def login(self):
        self.logged_in = True

    def log(self, data, *args, **kwargs):
        self.logged.append((data, args, kwargs))

    def init(self, *args, **kwargs):
        return self.run


def test_stubbed_wandb_init_traces_run_and_log(monkeypatch):
    fake_tracer = _FakeTracer()
    fake_wandb = _FakeWandb()
    original_init = fake_wandb.init
    original_log = fake_wandb.log
    original_login = fake_wandb.login
    monkeypatch.setattr(wandb_patch, "wandb", fake_wandb)
    monkeypatch.setattr(wandb_patch, "tracer", fake_tracer)

    wandb_patch.patch()

    fake_wandb.login()
    fake_wandb.log({"ignored": 1})
    assert fake_wandb.logged_in is False
    assert fake_wandb.logged == []

    with fake_wandb.init(project="my-awesome-project", config={"epochs": 10, "lr": 0.01}) as run:
        run.log({"accuracy": 0.98, "loss": 0.02})

    wandb_patch.unpatch()
    assert fake_wandb.init == original_init
    assert fake_wandb.log == original_log
    assert fake_wandb.login == original_login

    assert len(fake_tracer.spans) == 2
    run_span, log_span = fake_tracer.spans

    assert run_span.name == "wandb.run"
    assert run_span.finished is True
    assert run_span.tags["wandb.project"] == "my-awesome-project"
    assert _is_wandb_run_id(run_span.tags["wandb.run_id"])
    assert run_span.tags["wandb.config.epochs"] == "10"
    assert run_span.tags["wandb.config.lr"] == "0.01"

    assert log_span.name == "wandb.log"
    assert log_span.finished is True
    assert _is_wandb_run_id(log_span.tags["wandb.run_id"])
    assert log_span.tags["wandb.log.accuracy"] == "0.98"
    assert log_span.tags["wandb.log.loss"] == "0.02"


def test_non_drop_in_monkey_patch_preserves_wandb_behavior(monkeypatch):
    fake_tracer = _FakeTracer()
    fake_wandb = _FakeWandb()
    original_init = fake_wandb.init
    original_log = fake_wandb.log
    original_login = fake_wandb.login
    monkeypatch.setattr(wandb_patch, "wandb", fake_wandb)
    monkeypatch.setattr(wandb_patch.config.wandb, "drop_in", False)
    monkeypatch.setattr(wandb_patch, "tracer", fake_tracer)

    wandb_patch.patch()

    fake_wandb.login()
    fake_wandb.log({"kept": 1})
    assert fake_wandb.logged_in is True
    assert fake_wandb.logged == [({"kept": 1}, (), {})]

    with fake_wandb.init(project="my-awesome-project", config={"epochs": 10, "lr": 0.01}) as run:
        run.log({"accuracy": 0.98, "loss": 0.02})

    wandb_patch.unpatch()
    assert fake_wandb.init == original_init
    assert fake_wandb.log == original_log
    assert fake_wandb.login == original_login

    assert len(fake_tracer.spans) == 2
    run_span, log_span = fake_tracer.spans

    assert run_span.name == "wandb.run"
    assert run_span.finished is True
    assert run_span.tags["wandb.project"] == "my-awesome-project"
    assert run_span.tags["wandb.run_id"] == "run-123"
    assert run_span.tags["wandb.config.epochs"] == "10"
    assert run_span.tags["wandb.config.lr"] == "0.01"

    assert log_span.name == "wandb.log"
    assert log_span.finished is True
    assert log_span.tags["wandb.run_id"] == "run-123"
    assert log_span.tags["wandb.log.accuracy"] == "0.98"
    assert log_span.tags["wandb.log.loss"] == "0.02"
