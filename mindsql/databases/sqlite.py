import sqlite3
import pandas as pd
from typing import List
from .idatabase import IDatabase
from .._utils import logger
from .._utils.constants import ERROR_CONNECTING_TO_DB_CONSTANT, INVALID_DB_CONNECTION_OBJECT

log = logger.init_loggers("SQLite")

class SQLite(IDatabase):
    def create_connection(self, url: str, **kwargs) -> any:
        """
        Creates a connection to a SQLite database.
        URL format: sqlite:///path/to/database.db
        """
        try:
            db_path = url.replace("sqlite:///", "")
            connection = sqlite3.connect(db_path, check_same_thread=False)
            return connection
        except Exception as e:
            log.error(ERROR_CONNECTING_TO_DB_CONSTANT.format("SQLite", e))
            raise e

    def validate_connection(self, connection: any) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise ValueError(INVALID_DB_CONNECTION_OBJECT.format("SQLite"))

    def execute_sql(self, connection: any, sql: str, params: tuple = None) -> pd.DataFrame:
        try:
            self.validate_connection(connection)
            if params:
                df = pd.read_sql_query(sql, connection, params=params)
            else:
                df = pd.read_sql_query(sql, connection)
            return df
        except Exception as e:
            log.error(f"SQLite execution error: {e}")
            raise e

    def get_databases(self, connection) -> List[str]:
        return ["main"]

    def get_table_names(self, connection, database: str) -> pd.DataFrame:
        query = "SELECT name as 'Table' FROM sqlite_master WHERE type='table';"
        return self.execute_sql(connection, query)

    def get_all_ddls(self, connection: any, database: str) -> pd.DataFrame:
        query = "SELECT name as 'Table', sql as 'DDL' FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
        return self.execute_sql(connection, query)

    def get_ddl(self, connection: any, table_name: str, **kwargs) -> str:
        query = f"SELECT sql FROM sqlite_master WHERE type='table' AND name = ?;"
        df = self.execute_sql(connection, query, (table_name,))
        if not df.empty:
            return df.iloc[0]['sql']
        return ""

    def get_table_metadata(self, connection, table_name: str) -> dict:
        self.validate_connection(connection)
        
        # Get column info
        df_cols = self.execute_sql(connection, f"PRAGMA table_info({table_name});")
        
        # Get row count
        df_count = self.execute_sql(connection, f"SELECT COUNT(*) as cnt FROM {table_name};")
        row_count = int(df_count['cnt'].iloc[0]) if not df_count.empty else 0
        
        columns = {}
        for _, row in df_cols.iterrows():
            columns[row['name']] = {"type": row['type']}
            
        return {"columns": columns, "metadata": {"row_count": row_count}}

    def get_dialect(self) -> str:
        return "sqlite"

    def get_foreign_keys(self, connection: any, database: str) -> pd.DataFrame:
        # SQLite FKs are handled per table
        tables_df = self.get_table_names(connection, database)
        all_fks = []
        
        for table in tables_df['Table']:
            fk_df = self.execute_sql(connection, f"PRAGMA foreign_key_list({table});")
            for _, row in fk_df.iterrows():
                all_fks.append({
                    "table_name": table,
                    "column_name": row['from'],
                    "foreign_table_name": row['table'],
                    "foreign_column_name": row['to']
                })
        
        return pd.DataFrame(all_fks)
