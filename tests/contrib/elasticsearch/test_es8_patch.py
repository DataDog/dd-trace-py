import elasticsearch

from ddtrace.contrib.elasticsearch.patch import patch
from tests.utils import TracerTestCase


class ElasticsearchPatchTest(TracerTestCase):
    def test_patch():
        # This is to stop crashing applications if ES8 is used
        # If it doesn't fail, we are good.
        patch()
