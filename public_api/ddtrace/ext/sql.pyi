from typing import Any

QUERY: str
ROWS: str
DB: str

def normalize_vendor(vendor: Any): ...
def parse_pg_dsn(dsn: Any): ...
