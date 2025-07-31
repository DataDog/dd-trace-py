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

