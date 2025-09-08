import bm
import duckdb


class DuckDBScenario(bm.Scenario):
    tracer_enabled: bool
    query_type: str
    dataset_size: int

    def run(self):
        if self.tracer_enabled:
            from ddtrace import patch
            patch(duckdb=True)
        
        # Create connection and setup data based on scenario
        conn = duckdb.connect(":memory:")
        cursor = conn.cursor()
        
        if self.query_type == "simple":
            # Simple query benchmark
            def _(loops):
                for _ in range(loops):
                    cursor.execute("SELECT ? as value", (42,))
                    rows = cursor.fetchall()
                    assert len(rows) == 1
        
        elif self.query_type == "bulk_insert":
            # Bulk insert benchmark
            cursor.execute("CREATE TABLE bulk_test (id INTEGER, value VARCHAR, timestamp TIMESTAMP)")
            test_data = [(i, f"value_{i}", "2023-01-01 00:00:00") for i in range(self.dataset_size)]
            
            def _(loops):
                for _ in range(loops):
                    cursor.executemany("INSERT INTO bulk_test VALUES (?, ?, ?)", test_data)
                    # Clean up for next iteration
                    cursor.execute("DELETE FROM bulk_test")
        
        elif self.query_type == "analytical":
            # Analytical query benchmark
            cursor.execute(f"""
                CREATE TABLE analytics_test AS 
                SELECT 
                    i as id,
                    i % 10 as category,
                    i * 1.5 as value,
                    date '2023-01-01' + (i % 365)::INTEGER as date_key
                FROM range({self.dataset_size}) t(i)
            """)
            
            analytical_query = """
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
            
            def _(loops):
                for _ in range(loops):
                    cursor.execute(analytical_query)
                    rows = cursor.fetchall()
                    assert len(rows) > 0
        
        elif self.query_type == "complex_join":
            # Complex JOIN benchmark
            cursor.execute(f"""
                CREATE TABLE customers AS
                SELECT 
                    i as customer_id,
                    'Customer_' || i as name,
                    ('Region_' || (i % 10)) as region
                FROM range(1, {self.dataset_size // 10 + 1}) t(i)
            """)
            
            cursor.execute(f"""
                CREATE TABLE orders AS
                SELECT 
                    i as order_id,
                    (i % {self.dataset_size // 10}) + 1 as customer_id,
                    (random() * 1000)::INTEGER as amount,
                    date '2023-01-01' + (i % 365)::INTEGER as order_date
                FROM range({self.dataset_size}) t(i)
            """)
            
            join_query = """
                SELECT 
                    c.region,
                    COUNT(o.order_id) as order_count,
                    SUM(o.amount) as total_amount,
                    AVG(o.amount) as avg_amount
                FROM customers c
                LEFT JOIN orders o ON c.customer_id = o.customer_id
                GROUP BY c.region
                ORDER BY total_amount DESC
            """
            
            def _(loops):
                for _ in range(loops):
                    cursor.execute(join_query)
                    rows = cursor.fetchall()
                    assert len(rows) > 0
        
        else:
            raise ValueError(f"Unknown query_type: {self.query_type}")
        
        yield _
        
        # Cleanup
        conn.close()

