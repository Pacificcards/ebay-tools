import os
import psycopg2


def get_connection() -> psycopg2.extensions.connection:
    """Return a Postgres connection to Supabase."""
    db_url = os.environ["SUPABASE_DB_URL"]
    return psycopg2.connect(db_url)
