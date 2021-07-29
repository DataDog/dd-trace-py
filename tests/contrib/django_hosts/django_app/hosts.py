from django_hosts import host
from django_hosts import patterns


host_patterns = patterns("", host(r"app\.example\.org", "tests.contrib.django_hosts.django_app.app_urls", name="app"))
