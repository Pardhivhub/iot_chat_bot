from typing import List

import pandas as pd
import psycopg2

from . import IDatabase
from .._utils import logger
try:
    from ...config import Config
except (ImportError, ValueError):
    try:
        from config import Config
    except ImportError:
        import sys
        import os
        sys.path.append(os.getcwd())
        from config import Config
from .._utils.constants import ERROR_CONNECTING_TO_DB_CONSTANT, INVALID_DB_CONNECTION_OBJECT, ERROR_WHILE_RUNNING_QUERY, \
    POSTGRESQL_SHOW_DATABASE_QUERY, POSTGRESQL_DB_TABLES_INFO_SCHEMA_QUERY, \
    POSTGRESQL_SHOW_CREATE_TABLE_QUERY, CONNECTION_ESTABLISH_ERROR_CONSTANT

log = logger.init_loggers("Postgres")


class Postgres(IDatabase):
    def create_connection(self, url: str, **kwargs) -> any:
        """
        Connects to a PostgreSQL database using the provided URL.

        Parameters:
            - url (str): The URL in the format postgresql://username:password@host:port/database_name
            - **kwargs: Additional keyword arguments for the connection

        Returns:
            - connection: A connection to the PostgreSQL database

        Exceptions:
            - psycopg2.OperationalError: If an error occurs while connecting to the PostgreSQL database
        """
        try:
            print("Connecting to database...")
            # Use urlparse to handle potential special characters in password/host
            from urllib.parse import urlparse, unquote
            parsed = urlparse(url)
            
            # Extract credentials safely
            db_kwargs = {
                "user": unquote(parsed.username) if parsed.username else None,
                "password": unquote(parsed.password) if parsed.password else None,
                "host": parsed.hostname,
                "port": parsed.port,
                "dbname": unquote(parsed.path.lstrip('/')) if parsed.path else None
            }
            # Remove None values
            db_kwargs = {k: v for k, v in db_kwargs.items() if v is not None}
            
            log.info(f"Attempting connection to {db_kwargs.get('host')} as user {db_kwargs.get('user')}...")
            if "password" not in db_kwargs:
                log.warning("No password provided in connection string!")
            
            connection = psycopg2.connect(**db_kwargs)
            connection.autocommit = True  # Ensure read-only queries don't hang in transactions
            
            # Force the connection to use the configured schema (e.g., 'itciot')
            # This prevents the DB from defaulting to 'public' for table extraction
            schema = Config.DATABASE_SCHEMA
            cur = connection.cursor()
            from psycopg2 import sql
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            self.current_schema = schema
            cur.close()
            
            log.info(f"Active schema set to: {self.current_schema}")
            print(f"Active schema set to: {self.current_schema}")
            
            return connection
        except psycopg2.OperationalError as e:
            log.error(ERROR_CONNECTING_TO_DB_CONSTANT.format("PostgreSQL", e))
            raise e

    def validate_connection(self, connection: any) -> None:
        """
        A function that validates if the provided connection is a PostgreSQL connection.

        Parameters:
            connection: The connection object for accessing the database.

        Raises:
            ValueError: If the provided connection is not a PostgreSQL connection.
        """
        if connection is None:
            raise ValueError(CONNECTION_ESTABLISH_ERROR_CONSTANT)
        if not isinstance(connection, psycopg2.extensions.connection):
            raise ValueError(INVALID_DB_CONNECTION_OBJECT.format("PostgreSQL"))

    def execute_sql(self, connection: any, sql: str, params: tuple = None) -> pd.DataFrame:
        """
        Runs an SQL query using parameterized execution to prevent SQL injection.
        """
        try:
            self.validate_connection(connection)
            cursor = connection.cursor()
            cursor.execute(sql, params)
            
            if cursor.description is not None:
                results = cursor.fetchall()
                column_names = [desc[0] for desc in cursor.description]
                df = pd.DataFrame(results, columns=column_names)
            else:
                df = pd.DataFrame()
                
            cursor.close()
            return df
        except psycopg2.Error as e:
            connection.rollback()
            log.error(ERROR_WHILE_RUNNING_QUERY.format(e))
            raise Exception(f"Database Error: {str(e)}")

    def get_databases(self, connection) -> List[str]:
        """
        Get a list of databases from the given connection and SQL query.

        Parameters:
            connection: The connection object for the database.

        Returns:
            List[str]: A list of unique database names.
        """
        try:
            self.validate_connection(connection)
            df_databases = self.execute_sql(connection=connection, sql=POSTGRESQL_SHOW_DATABASE_QUERY)
            
            # Case-insensitive column search
            col_name = next((c for c in df_databases.columns if c.upper() == 'DATABASE_NAME'), None)
            if not col_name:
                log.warning(f"DATABASE_NAME column not found. Available: {list(df_databases.columns)}")
                return []
                
            return df_databases[col_name].unique().tolist()
        except Exception as e:
            log.info(e)
            return []

    def get_table_names(self, connection, database: str) -> pd.DataFrame:
        """
        Retrieves the tables from the information schema for the specified database.

        Parameters:
            connection: The database connection object.
            database (str): The name of the database.

        Returns:
            DataFrame: A pandas DataFrame containing the table names from the information schema.
        """
        self.validate_connection(connection)
        
        # Get current schema if not already detected
        schema = getattr(self, 'current_schema', Config.DATABASE_SCHEMA)
        
        query = POSTGRESQL_DB_TABLES_INFO_SCHEMA_QUERY.format(schema=schema, db=database)
        df_tables = self.execute_sql(connection, query)
        return df_tables

    def get_all_ddls(self, connection, database: str) -> pd.DataFrame:
        """
        A method to get the DDLs for all the tables in the database.

        Parameters:
            connection (any): The connection object.
            database (str): The name of the database.

        Returns:
            DataFrame: A pandas DataFrame containing the DDLs for all the tables in the database.
        """
        self.validate_connection(connection)
        df_tables = self.get_table_names(connection, database)
        df_ddl = pd.DataFrame(columns=['Table', 'DDL'])
        for index, row in df_tables.iterrows():
            table_name = row.get('table_name')
            ddl_df = self.get_ddl(connection, table_name)
            df_ddl = pd.concat([df_ddl, pd.DataFrame([{'Table': table_name, 'DDL': ddl_df}])], ignore_index=True)
        return df_ddl

    def get_ddl(self, connection, table_name: str, **kwargs) -> str:
        """
        A method to get the DDL for the table.

        Parameters:
            connection (any): The connection object.
            table_name (str): The name of the table.

        Returns:
            str: The DDL for the table.
        """
        self.validate_connection(connection)
        try:
            schema = getattr(self, 'current_schema', Config.DATABASE_SCHEMA)
            ddl_df = self.execute_sql(connection, POSTGRESQL_SHOW_CREATE_TABLE_QUERY.format(table=table_name, schema=schema))
            if ddl_df is not None and not ddl_df.empty and 'create_statement' in ddl_df.columns:
                return ddl_df['create_statement'].iloc[0]
            else:
                log.warning(f"No DDL found for table: {table_name}")
                return f"-- No DDL found for table {table_name}"
        except Exception as e:
            log.error(f"Error getting DDL for table {table_name}: {e}")
            return f"-- Error getting DDL for table {table_name}: {str(e)}"

    def get_table_metadata(self, connection, table_name: str) -> dict:
        """
        Get table metadata including columns and row count.
        
        Parameters:
            connection: The database connection object.
            table_name (str): The name of table.
            
        Returns:
            dict: Table metadata with columns and row count.
        """
        self.validate_connection(connection)
        
        # Get current schema if not already detected
        schema = getattr(self, 'current_schema', Config.DATABASE_SCHEMA)
        
        # Get column information using parameterized query
        column_query = """
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = %s AND table_schema = %s
        ORDER BY ordinal_position;
        """
        df_columns = self.execute_sql(connection, column_query, (table_name, schema))
        
        # Get row count using parameterized query with proper schema qualification
        count_query = f"""
        SELECT n_live_tup as row_count 
        FROM pg_stat_user_tables 
        WHERE relname = %s AND schemaname = %s;
        """
        df_count = self.execute_sql(connection, count_query, (table_name, schema))
        
        columns = {}
        if df_columns is not None and not df_columns.empty:
            for _, row in df_columns.iterrows():
                columns[row['column_name']] = {"type": row['data_type']}
        
        row_count = 0
        if df_count is not None and not df_count.empty:
            row_count = df_count['row_count'].iloc[0]
        
        return {
            "columns": columns,
            "metadata": {"row_count": row_count}
        }

    def get_dialect(self) -> str:
        return 'postgres'

    def get_foreign_keys(self, connection, database: str) -> pd.DataFrame:
        """
        Retrieves foreign key relationships from the information schema.
        
        Returns columns: [table_name, column_name, foreign_table_name, foreign_column_name]
        """
        self.validate_connection(connection)
        
        # Get current schema if not already detected
        schema = getattr(self, 'current_schema', Config.DATABASE_SCHEMA)
        
        query = """
        SELECT
            tc.table_name, 
            kcu.column_name, 
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name 
        FROM 
            information_schema.table_constraints AS tc 
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
              AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' 
          AND LOWER(tc.table_catalog) = LOWER(%s)
          AND tc.table_schema = %s;
        """
        return self.execute_sql(connection, query, (database, schema))
