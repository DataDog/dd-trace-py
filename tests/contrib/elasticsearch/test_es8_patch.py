from ddtrace.contrib.elasticsearch.patch import patch
from tests.utils import TracerTestCase


class ElasticsearchPatchTest(TracerTestCase):
    def test_patch(self):
        # This is to stop crashing applications if ES8 is used
        # If it doesn't error, we are good.
        patch()
