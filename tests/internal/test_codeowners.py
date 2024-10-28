from ddtrace.internal.codeowners import Codeowners


def test_invalid_codeowners(testdir):
    """Skip invalid lines and still match valid rules."""
    codeowners = """
    [invalid section
    * @default

    ^[invalid optional section
    bar.py @bars
    # Inline comment case
    baz.py @DataDog/the-owner  # all that should be ignored
    """
    codeowners_file = testdir.makefile("", CODEOWNERS=codeowners)

    c = Codeowners(path=codeowners_file.strpath)
    assert c.of("foo.py") == ["@default"]
    assert c.of("bar.py") == ["@bars"]
    assert c.of("baz.py") == ["@DataDog/the-owner"]
