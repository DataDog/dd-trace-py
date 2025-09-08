import time
import threading
import pytest
import duckdb

from ddtrace.contrib.internal.duckdb.patch import patch, unpatch
from ddtrace.constants import ERROR_MSG, ERROR_TYPE, ERROR_STACK
from ddtrace.trace import Pin
from tests.utils import TracerTestCase, assert_dict_issuperset, assert_is_measured


class DuckDBComprehensiveTestCase(TracerTestCase):
    """Comprehensive test suite for DuckDB integration"""

    def setUp(self):
        super(DuckDBComprehensiveTestCase, self).setUp()
        patch()

    def tearDown(self):
        super(DuckDBComprehensiveTestCase, self).tearDown()
        unpatch()

    def _get_conn_tracer(self):
        """Helper method to get connection and tracer"""
        conn = duckdb.connect(":memory:")
        pin = Pin.get_from(conn)
        assert pin
        pin._clone(tracer=self.tracer).onto(conn)
        return conn, self.tracer

    def test_simple_query(self):
        """Test basic query execution - span details covered by snapshot tests"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        cursor.execute("SELECT 42 as answer")
        rows = cursor.fetchall()
        
        assert len(rows) == 1
        assert rows[0][0] == 42
        
        spans = tracer.pop()
        assert len(spans) == 1

    def test_parameterized_query(self):
        """Test parameterized queries - span details covered by snapshot tests"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        # Test with positional parameters
        cursor.execute("SELECT ? as value", (123,))
        rows = cursor.fetchall()
        assert rows[0][0] == 123
        
        # Test with named parameters
        cursor.execute("SELECT $name as greeting", {"name": "Hello"})
        rows = cursor.fetchall()
        assert rows[0][0] == "Hello"
        
        spans = tracer.pop()
        assert len(spans) == 2

    def test_fetch_methods(self):
        """Test different fetch methods (fetchone, fetchmany, fetchall)"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        # Setup test data
        cursor.execute("CREATE TABLE test_fetch (id INTEGER, name VARCHAR)")
        cursor.execute("INSERT INTO test_fetch VALUES (1, 'Alice'), (2, 'Bob'), (3, 'Charlie')")
        
        # Test fetchone
        cursor.execute("SELECT * FROM test_fetch ORDER BY id")
        row = cursor.fetchone()
        assert row == (1, 'Alice')
        
        # Test fetchmany
        cursor.execute("SELECT * FROM test_fetch ORDER BY id")
        rows = cursor.fetchmany(2)
        assert len(rows) == 2
        assert rows[0] == (1, 'Alice')
        assert rows[1] == (2, 'Bob')
        
        # Test fetchall
        cursor.execute("SELECT * FROM test_fetch ORDER BY id")
        rows = cursor.fetchall()
        assert len(rows) == 3
        
        spans = tracer.pop()
        assert len(spans) >= 5  # CREATE, INSERT, 3 SELECTs
        
        # Check that all SQL operations were traced
        sql_spans = [s for s in spans if s.name == "duckdb.query"]
        assert len(sql_spans) >= 5

    def test_bulk_operations(self):
        """Test bulk insert operations using executemany"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        cursor.execute("CREATE TABLE bulk_test (id INTEGER, value VARCHAR)")
        
        # Bulk insert data
        test_data = [(i, f"value_{i}") for i in range(100)]
        cursor.executemany("INSERT INTO bulk_test VALUES (?, ?)", test_data)
        
        # Verify data was inserted
        cursor.execute("SELECT COUNT(*) FROM bulk_test")
        count = cursor.fetchone()[0]
        assert count == 100
        
        spans = tracer.pop()
        assert len(spans) >= 2  # CREATE + executemany operations
        
        # Find the bulk insert span
        bulk_spans = [s for s in spans if "INSERT" in s.resource]
        assert len(bulk_spans) >= 1

    def test_large_result_set(self):
        """Test performance with large result sets"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        cursor.execute("CREATE TABLE large_test (id INTEGER, data VARCHAR)")
        
        # Insert large dataset
        cursor.execute("""
            INSERT INTO large_test 
            SELECT range as id, 'data_' || range as data 
            FROM range(10000)
        """)
        
        start_time = time.time()
        cursor.execute("SELECT * FROM large_test WHERE id % 100 = 0")
        rows = cursor.fetchall()
        end_time = time.time()
        
        assert len(rows) == 100  # Every 100th row 
        assert end_time - start_time < 5.0  # Should complete in reasonable time
        
        spans = tracer.pop()
        query_spans = [s for s in spans if s.name == "duckdb.query"]
        assert len(query_spans) >= 2

    def test_syntax_error_handling(self):
        """Test handling of SQL syntax errors"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        with pytest.raises(Exception) as exc_info:
            cursor.execute("SELCT * FROM nonexistent")  # Typo in SELECT
        
        assert "syntax error" in str(exc_info.value).lower() or "parser error" in str(exc_info.value).lower()
        
        spans = tracer.pop()
        assert len(spans) == 1
        
        span = spans[0]
        assert span.error == 1
        assert span.get_tag(ERROR_TYPE) 
        assert span.get_tag(ERROR_MSG)

    def test_table_not_found_error(self):
        """Test handling of table not found errors"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        with pytest.raises(Exception) as exc_info:
            cursor.execute("SELECT * FROM nonexistent_table")
        
        assert "does not exist" in str(exc_info.value) or "not found" in str(exc_info.value).lower()
        
        spans = tracer.pop()
        assert len(spans) == 1
        
        span = spans[0]
        assert span.error == 1

    def test_constraint_violation_error(self):
        """Test handling of constraint violation errors"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        cursor.execute("CREATE TABLE constraint_test (id INTEGER PRIMARY KEY, value VARCHAR)")
        cursor.execute("INSERT INTO constraint_test VALUES (1, 'first')")
        
        # Try to insert duplicate primary key
        with pytest.raises(Exception) as exc_info:
            cursor.execute("INSERT INTO constraint_test VALUES (1, 'duplicate')")
        
        spans = tracer.pop()
        error_spans = [s for s in spans if s.error == 1]
        assert len(error_spans) == 1

    def test_connection_lifecycle(self):
        """Test connection creation, usage, and cleanup"""
        # Test multiple connections
        conn1, tracer = self._get_conn_tracer()
        conn2 = duckdb.connect(":memory:")
        pin = Pin.get_from(conn2)
        pin._clone(tracer=self.tracer).onto(conn2)
        
        cursor1 = conn1.cursor()
        cursor2 = conn2.cursor()
        
        cursor1.execute("SELECT 'conn1' as source")
        cursor2.execute("SELECT 'conn2' as source")
        
        result1 = cursor1.fetchone()[0]
        result2 = cursor2.fetchone()[0]
        
        assert result1 == 'conn1'
        assert result2 == 'conn2'
        
        # Close connections
        conn1.close()
        conn2.close()
        
        spans = tracer.pop()
        assert len(spans) == 2

    def test_concurrent_operations(self):
        """Test concurrent database operations"""
        conn, tracer = self._get_conn_tracer()
        
        # DuckDB allows concurrent reads from the same connection
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE concurrent_test (id INTEGER, thread_id INTEGER)")
        
        def worker(thread_id):
            cursor = conn.cursor()
            for i in range(10):
                cursor.execute("INSERT INTO concurrent_test VALUES (?, ?)", (i, thread_id))
        
        threads = []
        for i in range(3):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # Verify all data was inserted
        cursor.execute("SELECT COUNT(*) FROM concurrent_test")
        count = cursor.fetchone()[0]
        assert count == 30  # 3 threads × 10 inserts each
        
        spans = tracer.pop()
        assert len(spans) >= 31  # CREATE + 30 INSERTs

    def test_transaction_handling(self):
        """Test transaction operations (BEGIN, COMMIT, ROLLBACK)"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        cursor.execute("CREATE TABLE transaction_test (id INTEGER, value VARCHAR)")
        
        # Test successful transaction
        cursor.execute("BEGIN TRANSACTION")
        cursor.execute("INSERT INTO transaction_test VALUES (1, 'test')")
        cursor.execute("COMMIT")
        
        # Verify data was committed
        cursor.execute("SELECT COUNT(*) FROM transaction_test")
        count = cursor.fetchone()[0]
        assert count == 1
        
        # Test rollback transaction
        cursor.execute("BEGIN TRANSACTION")
        cursor.execute("INSERT INTO transaction_test VALUES (2, 'rollback')")
        cursor.execute("ROLLBACK")
        
        # Verify data was rolled back
        cursor.execute("SELECT COUNT(*) FROM transaction_test")
        count = cursor.fetchone()[0]
        assert count == 1  # Still only the first record
        
        spans = tracer.pop()
        assert len(spans) >= 7

    def test_service_name_override(self):
        """Test custom service name configuration - span details covered by snapshot tests"""
        conn = duckdb.connect(":memory:")
        pin = Pin.get_from(conn)
        custom_pin = pin._clone(service="custom-duckdb", tracer=self.tracer)
        custom_pin.onto(conn)
        
        cursor = conn.cursor()
        cursor.execute("SELECT 'custom' as test")
        rows = cursor.fetchall()
        assert rows[0][0] == 'custom'
        
        spans = self.tracer.pop()
        assert len(spans) == 1

    def test_file_database_connection(self):
        """Test connection to file-based database"""
        import tempfile
        import os
        
        # Create a temporary path for the database (don't create the file)
        with tempfile.NamedTemporaryFile(suffix='.duckdb', delete=True) as tmp:
            db_path = tmp.name
        
        try:
            conn = duckdb.connect(db_path)
            pin = Pin.get_from(conn)
            pin._clone(tracer=self.tracer).onto(conn)
            
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE file_test (id INTEGER)")
            cursor.execute("INSERT INTO file_test VALUES (42)")
            
            cursor.execute("SELECT * FROM file_test")
            result = cursor.fetchone()[0]
            assert result == 42
            
            conn.close()
            
            spans = self.tracer.pop()
            assert len(spans) == 3
            
            # Check that the database path is correctly tagged
            for span in spans:
                assert span.get_tag("db.name") == db_path
                
        finally:
            # Clean up temporary file
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_complex_queries(self):
        """Test complex SQL queries with joins and aggregations"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        # Setup test schema
        cursor.execute("""
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY, 
                name VARCHAR, 
                city VARCHAR
            )
        """)
        
        cursor.execute("""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER,
                amount DECIMAL,
                order_date DATE
            )
        """)
        
        # Insert test data
        cursor.execute("INSERT INTO customers VALUES (1, 'Alice', 'New York'), (2, 'Bob', 'Los Angeles')")
        cursor.execute("INSERT INTO orders VALUES (1, 1, 100.50, '2023-01-01'), (2, 1, 200.75, '2023-01-02'), (3, 2, 150.25, '2023-01-03')")
        
        # Complex query with JOIN and aggregation
        cursor.execute("""
            SELECT c.name, c.city, COUNT(o.id) as order_count, SUM(o.amount) as total_amount
            FROM customers c
            LEFT JOIN orders o ON c.id = o.customer_id
            GROUP BY c.id, c.name, c.city
            ORDER BY total_amount DESC
        """)
        
        results = cursor.fetchall()
        assert len(results) == 2
        assert results[0][0] == 'Alice'  # Alice should be first (highest total)
        assert results[0][2] == 2  # Alice has 2 orders
        
        spans = tracer.pop()
        query_spans = [s for s in spans if s.name == "duckdb.query"]
        assert len(query_spans) >= 5