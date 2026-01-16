import flask

from ddtrace.contrib.internal.flask.patch import unpatch

from . import BaseFlaskTestCase


class FlaskBlueprintTestCase(BaseFlaskTestCase):
    def test_patch(self):
        """
        When we patch Flask
            Then ``flask.Blueprint.register`` is patched
            Then ``flask.Blueprint.add_url_rule`` is patched
        """
        # DEV: We call `patch` in `setUp`
        self.assert_is_wrapped(flask.Blueprint.register)
        self.assert_is_wrapped(flask.Blueprint.add_url_rule)

    def test_unpatch(self):
        """
        When we unpatch Flask
            Then ``flask.Blueprint.register`` is not patched
            Then ``flask.Blueprint.add_url_rule`` is not patched
        """
        unpatch()
        self.assert_is_not_wrapped(flask.Blueprint.register)
        self.assert_is_not_wrapped(flask.Blueprint.add_url_rule)

    def test_blueprint_request(self):
        """
        When making a request to a Blueprint's endpoint
            We create the expected spans
        """
        bp = flask.Blueprint("bp", __name__)

        @bp.route("/")
        def test():
            return "test"

        self.app.register_blueprint(bp)

        # Request the endpoint
        self.client.get("/")

        # Only extract the span we care about
        # DEV: Making a request creates a bunch of lifecycle spans,
        #   ignore them, we test them elsewhere
        span = self.find_span_by_name(self.get_spans(), "bp.test")
        self.assertEqual(span.service, "flask")
        self.assertEqual(span.name, "bp.test")
        self.assertEqual(span.resource, "/")
        self.assertNotEqual(span.parent_id, 0)
        self.assertEqual(span.get_tags(), {"component": "flask", "_dd.base_service": "tests.contrib.flask"})
