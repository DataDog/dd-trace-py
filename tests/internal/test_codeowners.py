from ddtrace.internal.codeowners import Codeowners


def test_invalid_codeowners(testdir):
    """Skip invalid lines and still match valid rules."""
    codeowners = """
    [invalid section
    * @default

    ^[invalid optional section
    bar.py @bars
    """
    codeowners_file = testdir.makefile("", CODEOWNERS=codeowners)

    c = Codeowners(path=codeowners_file.strpath)
    assert c.of("foo.py") == ["@default"]
    assert c.of("bar.py") == ["@bars"]
