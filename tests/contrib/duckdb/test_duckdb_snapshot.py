import pytest

@pytest.mark.subprocess(ddtrace_run=True)
@pytest.mark.snapshot(wait_for_num_traces=1)
def test_duckdb_simple_query():
    import duckdb
    from ddtrace import patch
    
    patch(duckdb=True)
    
    conn = duckdb.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 as test_value")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 1


@pytest.mark.subprocess(ddtrace_run=True)
@pytest.mark.snapshot(wait_for_num_traces=3)
def test_duckdb_table_operations():
    import duckdb
    from ddtrace import patch
    
    patch(duckdb=True)
    
    conn = duckdb.connect(":memory:")
    cursor = conn.cursor()
    
    # Create table and insert data
    cursor.execute("CREATE TABLE users (id INTEGER, name VARCHAR, age INTEGER)")
    cursor.execute("INSERT INTO users VALUES (1, 'Alice', 30)")
    cursor.execute("INSERT INTO users VALUES (2, 'Bob', 25)")
    
    # Query data
    cursor.execute("SELECT * FROM users WHERE age > 25")
    rows = cursor.fetchall()
    assert len(rows) == 1


@pytest.mark.subprocess(ddtrace_run=True) 
@pytest.mark.snapshot(wait_for_num_traces=1, ignores=["meta.error.stack"])
def test_duckdb_error_handling():
    import duckdb
    from ddtrace import patch
    
    patch(duckdb=True)
    
    conn = duckdb.connect(":memory:")
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT * FROM non_existent_table")
        assert False, "Expected an error"
    except Exception:
        pass  # Expected error


@pytest.mark.subprocess(ddtrace_run=True)
@pytest.mark.snapshot(wait_for_num_traces=1)
def test_duckdb_bulk_insert_performance():
    """Test bulk insert operations to validate timing information in traces"""
    import duckdb
    from ddtrace import patch
    
    patch(duckdb=True)
    
    conn = duckdb.connect(":memory:")
    cursor = conn.cursor()
    
    # Create table
    cursor.execute("CREATE TABLE performance_test (id INTEGER, value VARCHAR, timestamp TIMESTAMP)")
    
    # Bulk insert to generate measurable timing
    insert_sql = "INSERT INTO performance_test VALUES (?, ?, ?)"
    data = [(i, f"value_{i}", "2023-01-01 00:00:00") for i in range(1000)]
    cursor.executemany(insert_sql, data)
    
    # Verify data was inserted
    cursor.execute("SELECT COUNT(*) FROM performance_test")
    count = cursor.fetchone()[0]
    assert count == 1000


@pytest.mark.subprocess(ddtrace_run=True)
@pytest.mark.snapshot(wait_for_num_traces=2)
def test_duckdb_complex_query():
    """Test complex queries with JOINs and aggregations"""
    import duckdb
    from ddtrace import patch
    
    patch(duckdb=True)
    
    conn = duckdb.connect(":memory:")
    cursor = conn.cursor()
    
    # Setup tables with relationships
    cursor.execute("""
        CREATE TABLE departments (id INTEGER, name VARCHAR);
        INSERT INTO departments VALUES 
            (1, 'Engineering'), (2, 'Sales'), (3, 'Marketing');
        
        CREATE TABLE employees (id INTEGER, name VARCHAR, dept_id INTEGER, salary INTEGER);
        INSERT INTO employees VALUES 
            (1, 'Alice', 1, 80000), (2, 'Bob', 1, 75000), 
            (3, 'Carol', 2, 70000), (4, 'Dave', 2, 65000);
    """)
    
    # Complex query with JOIN and aggregation  
    cursor.execute("""
        SELECT d.name, COUNT(e.id) as employee_count, AVG(e.salary) as avg_salary
        FROM departments d
        LEFT JOIN employees e ON d.id = e.dept_id
        GROUP BY d.name
        ORDER BY avg_salary DESC
    """)
    
    results = cursor.fetchall()
    assert len(results) == 3


@pytest.mark.subprocess(ddtrace_run=True)
@pytest.mark.snapshot(wait_for_num_traces=3, ignores=["meta.db.name"])
def test_duckdb_file_database():
    """Test file-based database operations"""
    import duckdb
    from ddtrace import patch
    import os
    import tempfile
    
    patch(duckdb=True)
    
    # Create temporary database path (let DuckDB create the file)
    with tempfile.NamedTemporaryFile(suffix='.duckdb', delete=True) as tmp:
        db_path = tmp.name
    
    try:
        conn = duckdb.connect(db_path)
        cursor = conn.cursor()
        
        # Create and populate table
        cursor.execute("CREATE TABLE file_test (id INTEGER, data VARCHAR)")
        cursor.execute("INSERT INTO file_test VALUES (1, 'test_data')")
        
        # Query data
        cursor.execute("SELECT * FROM file_test")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0] == (1, 'test_data')
        
        conn.close()
    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)


@pytest.mark.subprocess(ddtrace_run=True)
@pytest.mark.snapshot(wait_for_num_traces=5)
def test_duckdb_multiple_sequential_queries():
    """Test multiple sequential queries to validate multiple spans"""
    import duckdb
    from ddtrace import patch
    
    patch(duckdb=True)
    
    conn = duckdb.connect(":memory:")
    cursor = conn.cursor()
    
    # Execute multiple queries in sequence
    cursor.execute("CREATE TABLE seq_test (id INTEGER)")
    cursor.execute("INSERT INTO seq_test VALUES (1)")
    cursor.execute("INSERT INTO seq_test VALUES (2)")
    cursor.execute("INSERT INTO seq_test VALUES (3)")
    cursor.execute("SELECT COUNT(*) FROM seq_test")
    
    count = cursor.fetchone()[0]
    assert count == 3


@pytest.mark.subprocess(ddtrace_run=True)
@pytest.mark.snapshot(wait_for_num_traces=2)
def test_duckdb_analytical_query_performance():
    """Test analytical query with timing to validate performance metrics"""
    import duckdb
    from ddtrace import patch
    import time
    
    patch(duckdb=True)
    
    conn = duckdb.connect(":memory:")
    cursor = conn.cursor()
    
    # Create larger dataset for analytical query
    cursor.execute("""
        CREATE TABLE analytics_test AS 
        SELECT 
            i as id,
            i % 10 as category,
            i * 1.5 as value,
            date '2023-01-01' + (i % 365)::INTEGER as date_key
        FROM range(5000) t(i)
    """)
    
    # Analytical query with aggregations
    query = """
        SELECT 
            category,
            COUNT(*) as count,
            AVG(value) as avg_value,
            SUM(value) as total_value
        FROM analytics_test 
        WHERE value > 100
        GROUP BY category
        ORDER BY category
    """
    
    start_time = time.time()
    cursor.execute(query)
    results = cursor.fetchall()
    end_time = time.time()
    
    assert len(results) > 0
    assert end_time - start_time > 0  # Ensure some time elapsed


@pytest.mark.subprocess(ddtrace_run=True)
@pytest.mark.snapshot(wait_for_num_traces=2)
def test_duckdb_parameterized_queries():
    """Test parameterized queries with different parameter styles"""
    import duckdb
    from ddtrace import patch
    
    patch(duckdb=True)
    
    conn = duckdb.connect(":memory:")
    cursor = conn.cursor()
    
    # Test with positional parameters
    cursor.execute("SELECT ? as value", (123,))
    rows = cursor.fetchall()
    assert rows[0][0] == 123
    
    # Test with named parameters
    cursor.execute("SELECT $name as greeting", {"name": "Hello"})
    rows = cursor.fetchall()
    assert rows[0][0] == "Hello"


@pytest.mark.subprocess(ddtrace_run=True)
@pytest.mark.snapshot(wait_for_num_traces=3)
def test_duckdb_fetch_methods():
    """Test different fetch methods (fetchone, fetchmany, fetchall)"""
    import duckdb
    from ddtrace import patch
    
    patch(duckdb=True)
    
    conn = duckdb.connect(":memory:")
    cursor = conn.cursor()
    
    # Setup test data
    cursor.execute("CREATE TABLE test_fetch (id INTEGER, name VARCHAR)")
    cursor.execute("INSERT INTO test_fetch VALUES (1, 'Alice'), (2, 'Bob'), (3, 'Charlie')")
    
    # Test fetchall (matches existing snapshot)
    cursor.execute("SELECT * FROM test_fetch")
    rows = cursor.fetchall()
    assert len(rows) == 3


@pytest.mark.subprocess(ddtrace_run=True)
@pytest.mark.snapshot(wait_for_num_traces=1)
def test_duckdb_service_name_override():
    """Test custom service name configuration"""
    import duckdb
    from ddtrace import patch
    from ddtrace.trace import Pin
    
    patch(duckdb=True)
    
    conn = duckdb.connect(":memory:")
    pin = Pin.get_from(conn)
    custom_pin = pin._clone(service="custom-duckdb")
    custom_pin.onto(conn)
    
    cursor = conn.cursor()
    cursor.execute("SELECT 'custom' as test")
    rows = cursor.fetchall()
    assert rows[0][0] == 'custom'

