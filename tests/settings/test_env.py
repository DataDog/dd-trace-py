from ddtrace.internal.utils.formats import asbool
from ddtrace.settings.env import Env
from tests.utils import override_env


def test_env():
    class SubConfig(Env):
        bar = Env.var(bool, "BAR", parser=asbool, default=False)

    class Config(Env):
        foo = Env.var(int, "FOO", default=1)
        sub = SubConfig
        double_foo = Env.der(int, lambda env: env.foo * 2)

    with override_env(dict(FOO="2", BAR="true")):
        config = Config()
        assert config.foo == 2
        assert config.double_foo == config.foo * 2
        assert config.sub.bar is True
