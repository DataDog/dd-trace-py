import pytest

from ddtrace.contrib.internal.pytest._report_links import _get_base_url


# Test cases taken from <https://github.com/DataDog/datadog-ci/blob/v3.7.0/src/helpers/__tests__/app.test.ts>.
@pytest.mark.parametrize(
    "dd_site, dd_subdomain, expected",
    [
        # Usual datadog site.
        ("datadoghq.com", "", "https://app.datadoghq.com"),
        ("datadoghq.com", "app", "https://app.datadoghq.com"),
        ("datadoghq.com", "myorg", "https://myorg.datadoghq.com"),
        # Other datadog site.
        ("dd.datad0g.com", "", "https://dd.datad0g.com"),
        ("dd.datad0g.com", "dd", "https://dd.datad0g.com"),
        ("dd.datad0g.com", "myorg", "https://myorg.datad0g.com"),
        # Different top-level domain.
        ("datadoghq.eu", "", "https://app.datadoghq.eu"),
        ("datadoghq.eu", "app", "https://app.datadoghq.eu"),
        ("datadoghq.eu", "myorg", "https://myorg.datadoghq.eu"),
        # AP1/US3/US5-type datadog site: the datadog site's subdomain is replaced by `subdomain` when `subdomain` is custom.
        # The correct Main DC (US3 in this case) is resolved automatically.
        ("ap1.datadoghq.com", "", "https://ap1.datadoghq.com"),
        ("ap1.datadoghq.com", "app", "https://ap1.datadoghq.com"),
        ("ap1.datadoghq.com", "myorg", "https://myorg.datadoghq.com"),
        ("us3.datadoghq.com", "", "https://us3.datadoghq.com"),
        ("us3.datadoghq.com", "app", "https://us3.datadoghq.com"),
        ("us3.datadoghq.com", "myorg", "https://myorg.datadoghq.com"),
        ("us5.datadoghq.com", "", "https://us5.datadoghq.com"),
        ("us5.datadoghq.com", "app", "https://us5.datadoghq.com"),
        ("us5.datadoghq.com", "myorg", "https://myorg.datadoghq.com"),
    ],
)
def test_report_links_get_base_url(dd_site, dd_subdomain, expected):
    assert _get_base_url(dd_site, dd_subdomain) == expected
