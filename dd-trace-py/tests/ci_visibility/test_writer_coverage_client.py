"""Tests for CIVisibilityWriter coverage report client integration."""

from ddtrace.internal.ci_visibility.encoder import CIVisibilityCoverageReportEncoder
from ddtrace.internal.ci_visibility.writer import CIVisibilityAgentlessCoverageReportClient
from ddtrace.internal.ci_visibility.writer import CIVisibilityCoverageReportClient
from ddtrace.internal.ci_visibility.writer import CIVisibilityProxiedCoverageReportClient
from ddtrace.internal.ci_visibility.writer import CIVisibilityWriter


class TestCIVisibilityWriterCoverageClient:
    """Test suite for CIVisibilityWriter coverage report client functionality."""

    def test_writer_creates_coverage_client_when_enabled(self):
        """Test writer creates coverage report client when upload is enabled."""
        writer = CIVisibilityWriter(
            intake_url="http://test.datadog.com",
            coverage_report_upload_enabled=True,
        )

        # Should have created a coverage report client
        coverage_clients = [c for c in writer._clients if isinstance(c, CIVisibilityCoverageReportClient)]
        assert len(coverage_clients) == 1

    def test_writer_no_coverage_client_when_disabled(self):
        """Test writer doesn't create coverage client when upload is disabled."""
        writer = CIVisibilityWriter(
            intake_url="http://test.datadog.com",
            coverage_report_upload_enabled=False,
        )

        # Should not have created a coverage report client
        coverage_clients = [c for c in writer._clients if isinstance(c, CIVisibilityCoverageReportClient)]
        assert len(coverage_clients) == 0

    def test_writer_creates_agentless_coverage_client(self):
        """Test writer creates agentless coverage client by default."""
        writer = CIVisibilityWriter(
            intake_url="http://test.datadog.com",
            coverage_report_upload_enabled=True,
            use_evp=False,
        )

        # Should create agentless client
        agentless_clients = [c for c in writer._clients if isinstance(c, CIVisibilityAgentlessCoverageReportClient)]
        assert len(agentless_clients) == 1

    def test_writer_creates_proxied_coverage_client_with_evp(self):
        """Test writer creates proxied coverage client when using EVP."""
        writer = CIVisibilityWriter(
            intake_url="http://test.datadog.com",
            coverage_report_upload_enabled=True,
            use_evp=True,
        )

        # Should create proxied client
        proxied_clients = [c for c in writer._clients if isinstance(c, CIVisibilityProxiedCoverageReportClient)]
        assert len(proxied_clients) == 1

    def test_coverage_client_has_proper_encoder(self):
        """Test that coverage client has the proper encoder."""
        writer = CIVisibilityWriter(
            intake_url="http://test.datadog.com",
            coverage_report_upload_enabled=True,
        )

        coverage_clients = [c for c in writer._clients if isinstance(c, CIVisibilityCoverageReportClient)]

        client = coverage_clients[0]

        # Should have encoder property
        assert hasattr(client, "encoder")
        assert hasattr(client, "coverage_encoder")

        # coverage_encoder should return the encoder with correct type
        encoder = client.coverage_encoder
        assert isinstance(encoder, CIVisibilityCoverageReportEncoder)

        # Should have required methods
        assert hasattr(encoder, "encode_coverage_report")
        assert hasattr(encoder, "content_type")
        assert hasattr(encoder, "boundary")

    def test_coverage_client_initialization_with_headers(self):
        """Test coverage client initialization with custom headers."""
        writer = CIVisibilityWriter(
            intake_url="http://test.datadog.com",
            coverage_report_upload_enabled=True,
            headers={"Custom-Header": "test-value"},
        )

        coverage_clients = [c for c in writer._clients if isinstance(c, CIVisibilityCoverageReportClient)]

        client = coverage_clients[0]
        assert hasattr(client, "_headers")

    def test_coverage_client_initialization_with_intake_url(self):
        """Test coverage client initialization with intake URL."""
        test_url = "http://custom.test.com"
        writer = CIVisibilityWriter(
            intake_url=test_url,
            coverage_report_upload_enabled=True,
        )

        coverage_clients = [c for c in writer._clients if isinstance(c, CIVisibilityCoverageReportClient)]

        client = coverage_clients[0]
        assert hasattr(client, "_intake_url")
        assert client._intake_url == test_url

    def test_writer_recreate_preserves_coverage_settings(self):
        """Test that writer.recreate() preserves coverage report upload settings."""
        original_writer = CIVisibilityWriter(
            intake_url="http://test.datadog.com",
            coverage_report_upload_enabled=True,
            use_evp=True,
        )

        # Recreate the writer
        new_writer = original_writer.recreate()

        # Should preserve coverage settings
        assert new_writer._coverage_report_upload_enabled is True

        # Should have created coverage client
        coverage_clients = [c for c in new_writer._clients if isinstance(c, CIVisibilityCoverageReportClient)]
        assert len(coverage_clients) == 1

    def test_writer_both_coverage_and_coverage_reports_enabled(self):
        """Test writer with both regular coverage and coverage reports enabled."""
        writer = CIVisibilityWriter(
            intake_url="http://test.datadog.com",
            coverage_enabled=True,
            coverage_report_upload_enabled=True,
        )

        # Should have both types of clients
        coverage_clients = [c for c in writer._clients if c.__class__.__name__.endswith("CoverageClient")]
        coverage_report_clients = [c for c in writer._clients if c.__class__.__name__.endswith("CoverageReportClient")]

        assert len(coverage_clients) == 1  # Regular coverage client
        assert len(coverage_report_clients) == 1  # Coverage report client

    def test_coverage_report_client_encoder_property_type_safety(self):
        """Test that coverage_encoder property provides type safety."""
        client = CIVisibilityCoverageReportClient(intake_url="http://test.example.com")

        # The coverage_encoder property should return the correctly typed encoder
        encoder = client.coverage_encoder

        # Should be the correct type
        assert isinstance(encoder, CIVisibilityCoverageReportEncoder)

        # Should have all required methods and properties
        assert callable(encoder.encode_coverage_report)
        assert hasattr(encoder, "content_type")
        assert hasattr(encoder, "boundary")
        assert hasattr(encoder, "ENDPOINT_TYPE")

    def test_coverage_report_client_standalone_creation(self):
        """Test creating CIVisibilityCoverageReportClient directly."""
        intake_url = "http://direct.test.com"
        headers = {"Direct-Header": "direct-value"}

        client = CIVisibilityCoverageReportClient(intake_url=intake_url, headers=headers)

        assert client._intake_url == intake_url
        assert client._headers == headers

        # Should have encoder
        encoder = client.coverage_encoder
        assert isinstance(encoder, CIVisibilityCoverageReportEncoder)

    def test_agentless_coverage_report_client_endpoint(self):
        """Test that agentless coverage report client has correct endpoint."""
        client = CIVisibilityAgentlessCoverageReportClient(intake_url="http://test.example.com")

        from ddtrace.internal.ci_visibility.constants import COVERAGE_REPORT_UPLOAD_ENDPOINT

        assert hasattr(client, "ENDPOINT")
        assert client.ENDPOINT == COVERAGE_REPORT_UPLOAD_ENDPOINT

    def test_proxied_coverage_report_client_endpoint(self):
        """Test that proxied coverage report client has correct endpoint."""
        client = CIVisibilityProxiedCoverageReportClient(intake_url="http://test.example.com")

        from ddtrace.internal.ci_visibility.constants import COVERAGE_REPORT_UPLOAD_ENDPOINT

        assert hasattr(client, "ENDPOINT")
        assert client.ENDPOINT == COVERAGE_REPORT_UPLOAD_ENDPOINT

    def test_coverage_client_encoder_boundary_uniqueness(self):
        """Test that different client instances have unique encoder boundaries."""
        client1 = CIVisibilityCoverageReportClient(intake_url="http://test1.com")
        client2 = CIVisibilityCoverageReportClient(intake_url="http://test2.com")

        encoder1 = client1.coverage_encoder
        encoder2 = client2.coverage_encoder

        # Should have different boundaries
        assert encoder1.boundary != encoder2.boundary
        assert encoder1.content_type != encoder2.content_type
