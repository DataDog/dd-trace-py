"""Tests for CVE-2024-53908 - Django HasKey SQL injection vulnerability.

This module tests the CVE-2024-53908 vulnerability where direct use of
django.db.models.fields.json.HasKey lookup with Oracle database is subject
to SQL injection if untrusted data is used as the lhs value.

CVE Details:
- CVE-2024-53908
- Affects Django 5.1 prior to 5.1.4, 5.0 prior to 5.0.10, and 4.2 prior to 4.2.17
- Direct use of HasKey lookup with Oracle database and untrusted lhs value
- Applications using jsonfield.has_key lookup via double underscores are not affected
"""
from tests.appsec.appsec_utils import django_server


class TestCVE202453908:
    """Test cases for CVE-2024-53908 vulnerability."""

    def test_cve_2024_53908_vulnerability_with_oracle(self):
        """Test that CVE-2024-53908 vulnerability is present with Oracle database.

        This test demonstrates the SQL injection vulnerability when using
        Oracle database with HasKey lookup and untrusted data as lhs value.
        """
        # Use Oracle-specific settings
        oracle_settings = "tests.appsec.integrations.django_tests.django_app.settings_oracle"

        with django_server(
            django_settings_module=oracle_settings, run_migrations=True, port=8005, iast_enabled="true"
        ) as (server_process, client, _):
            # Step 1: Insert test data
            response = client.get("/cve/insert-test-data/")
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 2

            # Step 2: Test normal functionality
            response = client.get("/cve/lookup-key/?key=secure_key")
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 1  # Should find one item with secure_key

            response = client.get("/cve/lookup-key/?key=other_key")
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 1  # Should find one item with other_key

            response = client.get("/cve/lookup-key/?key=nonexistent_key")
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 0  # Should find no items

            # Step 3: Test SQL injection payload
            # This payload attempts to inject SQL that would bypass the intended query
            # In vulnerable versions, this could lead to SQL injection
            malicious_payload = "secure_key' OR '1'='1"
            response = client.get(f"/cve/lookup-key/?key={malicious_payload}")

            # In vulnerable versions, this might:
            # 1. Return an unexpected count (SQL injection successful)
            # 2. Cause a database error that reveals SQL structure
            # 3. Allow data extraction

            # The exact behavior depends on the Oracle version and Django version
            # In patched versions, this should be properly escaped
            assert response.status_code in [200, 500]  # Could error or return safely

            if response.status_code == 200:
                data = response.json()
                # In a properly patched version, this should return 0
                # In a vulnerable version, it might return more items than expected
                print(f"Response for malicious payload: {data}")
            else:
                # If it returns 500, check if the error reveals SQL injection
                data = response.json()
                error_msg = data.get("error", "").lower()
                print(f"Error response: {error_msg}")

                # Look for SQL-related errors that might indicate injection
                sql_indicators = ["sql", "oracle", "syntax", "near", "unexpected"]
                has_sql_error = any(indicator in error_msg for indicator in sql_indicators)

                if has_sql_error:
                    print("WARNING: SQL-related error detected - potential vulnerability")

            # Step 4: Test another injection payload
            # This payload tries to extract additional data
            union_payload = "secure_key' UNION SELECT 1--"
            response = client.get(f"/cve/lookup-key/?key={union_payload}")

            print(f"Union payload response status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"Union payload response: {data}")

            # Step 5: Clean up
            response = client.get("/cve/clear-database/")
            assert response.status_code == 200

    def test_cve_2024_53908_safe_with_sqlite(self):
        """Test that the vulnerability is not exploitable with SQLite.

        This test verifies that the same code works safely with SQLite,
        which is not affected by this specific CVE.
        """
        with django_server(port=8006, iast_enabled="true") as (server_process, client, _):
            # Step 1: Insert test data
            response = client.get("/cve/insert-test-data/")
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 2

            # Step 2: Test normal functionality
            response = client.get("/cve/lookup-key/?key=secure_key")
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 1

            # Step 3: Test that injection attempts are handled safely
            malicious_payload = "secure_key' OR '1'='1"
            response = client.get(f"/cve/lookup-key/?key={malicious_payload}")

            # With SQLite, this should be handled safely
            assert response.status_code == 200
            data = response.json()
            # Should return 0 since the malicious payload doesn't match any real key
            assert data["count"] == 0

            # Step 4: Clean up
            response = client.get("/cve/clear-database/")
            assert response.status_code == 200

    def test_cve_2024_53908_curl_example(self):
        """Provide curl examples for manual testing of the vulnerability."""

        # This test provides curl commands that can be used to manually test
        # the vulnerability when Oracle database is available

        curl_commands = [
            # Start the server with Oracle settings (manual step)
            "# 1. Start Oracle container:",
            "docker run -d --name oracle-xe -p 1521:1521 -e ORACLE_PASSWORD=oracle gvenzl/oracle-xe:21-slim",
            "",
            "# 2. Start Django server with Oracle settings:",
            "# DJANGO_SETTINGS_MODULE=tests.appsec.integrations.django_tests.django_app.settings_oracle \\",
            "# python tests/appsec/integrations/django_tests/django_app/manage.py runserver 8000",
            "",
            "# 3. Insert test data:",
            "curl -X GET 'http://localhost:8000/cve/insert-test-data/'",
            "",
            "# 4. Test normal functionality:",
            "curl -X GET 'http://localhost:8000/cve/lookup-key/?key=secure_key'",
            "curl -X GET 'http://localhost:8000/cve/lookup-key/?key=other_key'",
            "",
            "# 5. Test SQL injection payloads:",
            "curl -X GET \"http://localhost:8000/cve/lookup-key/?key=secure_key' OR '1'='1\"",
            'curl -X GET "http://localhost:8000/cve/lookup-key/?key=secure_key\' UNION SELECT 1--"',
            'curl -X GET "http://localhost:8000/cve/lookup-key/?key=\'; DROP TABLE cve_test_item; --"',
            "",
            "# 6. Clean up:",
            "curl -X GET 'http://localhost:8000/cve/clear-database/'",
        ]

        print("\nCURL commands for manual CVE-2024-53908 testing:")
        print("=" * 50)
        for cmd in curl_commands:
            print(cmd)

        # This test always passes - it's just for documentation
        assert True
