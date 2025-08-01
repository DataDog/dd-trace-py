import time
import threading
import pytest
import duckdb

from ddtrace.contrib.internal.duckdb.patch import patch, unpatch
from ddtrace.trace import Pin
from tests.utils import TracerTestCase


class DuckDBPerformanceTestCase(TracerTestCase):
    """Performance and stress testing for DuckDB integration"""

    def setUp(self):
        super(DuckDBPerformanceTestCase, self).setUp()
        patch()

    def tearDown(self):
        super(DuckDBPerformanceTestCase, self).tearDown()
        unpatch()

    def _get_conn_tracer(self):
        """Helper method to get connection and tracer"""
        conn = duckdb.connect(":memory:")
        pin = Pin.get_from(conn)
        pin._clone(tracer=self.tracer).onto(conn)
        return conn, self.tracer

    def test_large_table_creation_performance(self):
        """Test performance with large table creation"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        start_time = time.time()
        
        # Create a large table using DuckDB's range function
        cursor.execute("""
            CREATE TABLE large_perf_test AS 
            SELECT 
                range as id,
                'test_data_' || range as text_data,
                random() as random_value,
                (range % 1000) as category
            FROM range(100000)
        """)
        
        creation_time = time.time() - start_time
        
        # Verify table was created and has correct size
        cursor.execute("SELECT COUNT(*) FROM large_perf_test")
        count = cursor.fetchone()[0]
        assert count == 100000
        
        # Performance assertion - should complete in reasonable time
        assert creation_time < 10.0, f"Table creation took {creation_time:.2f}s, expected < 10s"
        
        spans = tracer.pop()
        assert len(spans) == 2  # CREATE and SELECT COUNT
        
        # Check that spans have reasonable durations
        create_span = next(s for s in spans if "CREATE" in s.resource)
        assert create_span.duration > 0

    def test_bulk_insert_performance(self):
        """Test performance of bulk insert operations"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        cursor.execute("CREATE TABLE bulk_perf_test (id INTEGER, value VARCHAR, timestamp TIMESTAMP)")
        
        # Generate test data (reduced size for Docker + tracing environment)
        test_data = [
            (i, f"bulk_value_{i}", "2023-01-01 00:00:00")
            for i in range(10000)
        ]
        
        start_time = time.time()
        cursor.executemany("INSERT INTO bulk_perf_test VALUES (?, ?, ?)", test_data)
        insert_time = time.time() - start_time
        
        # Verify all data was inserted
        cursor.execute("SELECT COUNT(*) FROM bulk_perf_test")
        count = cursor.fetchone()[0]
        assert count == 10000
        
        # Performance assertion (more realistic for Docker + tracing overhead)
        assert insert_time < 60.0, f"Bulk insert took {insert_time:.2f}s, expected < 60s"
        
        spans = tracer.pop()
        assert len(spans) >= 2

    def test_complex_query_performance(self):
        """Test performance of complex analytical queries"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        # Setup test data with multiple tables
        cursor.execute("""
            CREATE TABLE sales AS
            SELECT 
                range as sale_id,
                (range % 1000) + 1 as customer_id,
                (range % 100) + 1 as product_id,
                (random() * 1000)::INTEGER as amount,
                date '2023-01-01' + (range % 365)::INTEGER as sale_date
            FROM range(10000)
        """)
        
        cursor.execute("""
            CREATE TABLE customers AS
            SELECT 
                range as customer_id,
                'Customer_' || range as name,
                ('Region_' || (range % 10)) as region
            FROM range(1, 1001)
        """)
        
        cursor.execute("""
            CREATE TABLE products AS
            SELECT 
                range as product_id,
                'Product_' || range as name,
                ('Category_' || (range % 20)) as category
            FROM range(1, 101)
        """)
        
        # Complex analytical query
        start_time = time.time()
        cursor.execute("""
            SELECT 
                c.region,
                p.category,
                COUNT(*) as sale_count,
                SUM(s.amount) as total_amount,
                AVG(s.amount) as avg_amount,
                MAX(s.amount) as max_amount
            FROM sales s
            JOIN customers c ON s.customer_id = c.customer_id
            JOIN products p ON s.product_id = p.product_id
            WHERE s.sale_date >= date '2023-06-01'
            GROUP BY c.region, p.category
            HAVING COUNT(*) > 10
            ORDER BY total_amount DESC
            LIMIT 20
        """)
        
        results = cursor.fetchall()
        query_time = time.time() - start_time
        
        assert len(results) > 0
        assert query_time < 15.0, f"Complex query took {query_time:.2f}s, expected < 15s"
        
        spans = tracer.pop()
        query_spans = [s for s in spans if s.name == "duckdb.query"]
        assert len(query_spans) >= 4  # 3 CREATEs + 1 complex SELECT

    def test_concurrent_read_performance(self):
        """Test performance with concurrent read operations"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        # Setup shared test data
        cursor.execute("""
            CREATE TABLE concurrent_read_test AS
            SELECT 
                range as id,
                'data_' || range as value,
                (range % 100) as partition_key
            FROM range(20000)
        """)
        
        results = []
        
        def concurrent_reader(partition_id):
            cursor = conn.cursor()
            start_time = time.time()
            cursor.execute(
                "SELECT COUNT(*), AVG(id) FROM concurrent_read_test WHERE partition_key = ?",
                (partition_id,)
            )
            result = cursor.fetchone()
            end_time = time.time()
            results.append((partition_id, result, end_time - start_time))
        
        # Run concurrent reads
        threads = []
        start_time = time.time()
        
        for i in range(10):
            thread = threading.Thread(target=concurrent_reader, args=(i * 10,))
            threads.append(thread)
            thread.start()
        
        for thread in threads:
            thread.join()
        
        total_time = time.time() - start_time
        
        assert len(results) == 10
        assert total_time < 3.0, f"Concurrent reads took {total_time:.2f}s, expected < 3s"
        
        # Verify all queries returned valid results
        for partition_id, result, query_time in results:
            count, avg_id = result
            assert count > 0
            assert query_time < 1.0  # Individual query should be fast

    def test_memory_usage_large_resultset(self):
        """Test memory efficiency with large result sets"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE memory_test AS
            SELECT 
                range as id,
                repeat('x', 100) as large_text,
                random() as random_val
            FROM range(10000)
        """)
        
        # Test streaming large result set with fetchmany
        cursor.execute("SELECT * FROM memory_test ORDER BY id")
        
        total_rows = 0
        batch_size = 1000
        
        start_time = time.time()
        
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            total_rows += len(rows)
            
            # Simulate processing time
            time.sleep(0.001)
        
        processing_time = time.time() - start_time
        
        assert total_rows == 10000
        assert processing_time < 5.0, f"Streaming took {processing_time:.2f}s, expected < 5s"
        
        spans = tracer.pop()
        assert len(spans) >= 2

    def test_transaction_performance(self):
        """Test performance of transaction operations"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        cursor.execute("CREATE TABLE transaction_perf_test (id INTEGER, value VARCHAR)")
        
        # Test performance of many small transactions
        start_time = time.time()
        
        for i in range(100):
            cursor.execute("BEGIN TRANSACTION")
            cursor.execute("INSERT INTO transaction_perf_test VALUES (?, ?)", (i, f"value_{i}"))
            cursor.execute("COMMIT")
        
        transaction_time = time.time() - start_time
        
        # Verify all data was committed
        cursor.execute("SELECT COUNT(*) FROM transaction_perf_test")
        count = cursor.fetchone()[0]
        assert count == 100
        
        assert transaction_time < 5.0, f"100 transactions took {transaction_time:.2f}s, expected < 5s"
        
        spans = tracer.pop()
        # Should have CREATE + 100*(BEGIN + INSERT + COMMIT) + SELECT = 302 spans
        assert len(spans) >= 300

    def test_index_performance(self):
        """Test performance impact of indexes"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        # Create table with test data
        cursor.execute("""
            CREATE TABLE index_perf_test AS
            SELECT 
                range as id,
                (range % 1000) as search_key,
                'data_' || range as value
            FROM range(50000)
        """)
        
        # Query without index
        start_time = time.time()
        cursor.execute("SELECT COUNT(*) FROM index_perf_test WHERE search_key = 500")
        result_no_index = cursor.fetchone()[0]
        time_no_index = time.time() - start_time
        
        # Create index
        cursor.execute("CREATE INDEX idx_search_key ON index_perf_test(search_key)")
        
        # Query with index
        start_time = time.time()
        cursor.execute("SELECT COUNT(*) FROM index_perf_test WHERE search_key = 500")
        result_with_index = cursor.fetchone()[0]
        time_with_index = time.time() - start_time
        
        # Results should be the same
        assert result_no_index == result_with_index
        assert result_no_index > 0
        
        # Both queries should complete in reasonable time
        assert time_no_index < 2.0
        assert time_with_index < 2.0
        
        spans = tracer.pop()
        query_spans = [s for s in spans if s.name == "duckdb.query"]
        assert len(query_spans) >= 4  # CREATE table, 2 SELECTs, CREATE INDEX

    def test_aggregation_performance(self):
        """Test performance of complex aggregation queries"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE aggregation_perf_test AS
            SELECT 
                range as id,
                (range % 100) as group_key,
                (random() * 1000)::INTEGER as value1,
                (random() * 100)::INTEGER as value2,
                date '2023-01-01' + (range % 365)::INTEGER as date_key
            FROM range(25000)
        """)
        
        # Complex aggregation query
        start_time = time.time()
        cursor.execute("""
            SELECT 
                group_key,
                COUNT(*) as count,
                SUM(value1) as sum_val1,
                AVG(value1) as avg_val1,
                STDDEV(value1) as stddev_val1,
                MIN(value2) as min_val2,
                MAX(value2) as max_val2,
                COUNT(DISTINCT date_key) as unique_dates
            FROM aggregation_perf_test
            GROUP BY group_key
            HAVING COUNT(*) > 200
            ORDER BY sum_val1 DESC
        """)
        
        results = cursor.fetchall()
        aggregation_time = time.time() - start_time
        
        assert len(results) > 0
        assert aggregation_time < 10.0, f"Aggregation took {aggregation_time:.2f}s, expected < 10s"
        
        # Verify aggregation results make sense
        for row in results:
            group_key, count, sum_val1, avg_val1, stddev_val1, min_val2, max_val2, unique_dates = row
            assert count > 200  # Due to HAVING clause
            assert sum_val1 > 0
            assert avg_val1 > 0
            assert min_val2 >= 0
            assert max_val2 <= 100
        
        spans = tracer.pop()
        assert len(spans) >= 2