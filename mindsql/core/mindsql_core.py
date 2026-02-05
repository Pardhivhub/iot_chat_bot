import re
import sys
import json
import os
from typing import Union, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .. import _helper
from .._helper.helper import load_json_to_dict
from .._utils import prompts, logger
from .._utils.constants import NO_DATA_FOUND_IN_JSON_CONSTANT, \
    BULK_DATA_SUCCESS_MESSAGE_CONSTANT, SQL_NOT_PROVIDED_CONSTANT, ADD_QUESTION_SQL_MESSAGE_CONSTANT, \
    ADD_DOCS_MESSAGE_CONSTANT, ADD_DDL_MESSAGE_CONSTANT, BULK_FALSE_ERROR, DDL_PROCESSED_SUCCESSFULLY
from .._utils.prompts import DDL_PROMPT, FEW_SHOT_EXAMPLE, PLOTLY_PROMPT
from ..databases import IDatabase
from ..llms import ILlm
from ..vectorstores import IVectorstore
from .feedback_logger import FeedbackLogger
from .golden_cache import GoldenCache

log = logger.init_loggers("Minds Core")





class MindSQLCore:
    def __init__(self, database: IDatabase, vectorstore: IVectorstore, llm: ILlm) -> None:
        """
        Initialize the class with an optional config parameter.

        Returns:
            None
        """
        self.database = database
        self.vectorstore = vectorstore
        self.llm = llm
        self.feedback_logger = FeedbackLogger()
        self.golden_cache = GoldenCache()
        self.relationship_graph = {}  # 🚀 Dynamic Join Graph
        self.current_db = None       # 🚀 Cached Current DB

    def create_database_query(self, question: str, connection: any, tables: list[str], 
                             validation_error: Optional[str] = None, **kwargs) -> str:
        """
        A method to create the database query with type safety.
        """
        question_sql_list = self.vectorstore.retrieve_relevant_question_sql(question, **kwargs)
        prompt = self.build_sql_prompt(question=question, connection=connection, question_sql_list=question_sql_list,
                                       tables=tables, validation_error=validation_error, **kwargs)
        llm_response = self.llm.invoke(prompt, **kwargs)
        return _helper.helper.extract_sql(llm_response)

    @staticmethod
    def stuff_ddl_in_prompt(initial_prompt: str, ddl_list: list[str]) -> str:
        """
        A method to add DDL statements to the prompt.

        Parameters:
            initial_prompt (str): The initial prompt.
            ddl_list (list[str]): The list of DDL statements.

        Returns:
            str: The updated prompt with DDL statements.
        """
        if ddl_list:
            ddl_statements = "\n".join(ddl_list)
            prompt = f"{initial_prompt}\n{DDL_PROMPT.format(ddl_statements)}"
            return prompt
        return initial_prompt

    @staticmethod
    def stuff_documentation_in_prompt(initial_prompt: str, documentation_list: list[str]) -> str:
        """
        A method to add documentation statements to the prompt.

        Parameters:
            initial_prompt (str): The initial prompt.
            documentation_list (list[str]): The list of documentation statements.

        Returns:
            str: The updated prompt with documentation statements.
        """
        if documentation_list:
            doc_statements = "\n".join(documentation_list)
            prompt = f"{initial_prompt}\n{doc_statements}"
            return prompt
        return initial_prompt

    @staticmethod
    def stuff_sql_in_prompt(initial_prompt: str, sql_list: list[str]) -> str:
        """
        A method to add SQL statements to the prompt.

        Parameters:
            initial_prompt (str): The initial prompt.
            sql_list (list[str]): The list of SQL statements.

        Returns:
            str: The updated prompt with SQL statements.
        """
        if sql_list:
            sql_statements = "\n".join(sql_list)
            prompt = f"{initial_prompt}\n{sql_statements}"
            return prompt
        return initial_prompt

    def build_sql_prompt(self, question: str, connection: any, question_sql_list: list[str], tables: list[str],
                         validation_error: Optional[str] = None, **kwargs) -> str:
        """
        Builds a comprehensive prompt for the LLM, incorporating DDLs, 
        documentation, and optional relationship hints.
        """
        dialect_name = self.database.get_dialect()
        initial_prompt = self.__create_initial_prompt(question_sql_list, dialect_name)

        ddl_statements = self.__get_ddl_statements(connection, tables, question, **kwargs)
        initial_prompt = self.stuff_ddl_in_prompt(initial_prompt, ddl_statements)

        # 🔒 STEP 1 FIX: HARD GROUNDING
        tables_and_columns = self.__extract_tables_and_columns_from_ddl(ddl_statements)
        grounding_text = self.__create_grounding_text(tables_and_columns)
        initial_prompt = f"{initial_prompt}\n\n{grounding_text}"

        if validation_error:
            initial_prompt = f"{initial_prompt}\n\n### PREVIOUS ATTEMPT FAILURE:\nYour previous SQL attempt failed validation with the following error:\n{validation_error}\n\nPlease correct the SQL while strictly following Relationship Hints and Foreign Key constraints. Do NOT repeat the same mistake."

        doc_statements = self.vectorstore.retrieve_relevant_documentation(question, **kwargs)
        initial_prompt = self.stuff_documentation_in_prompt(initial_prompt, doc_statements)
        final_prompt = f"{initial_prompt}\n'Question': {question}"
        return final_prompt

    @staticmethod
    def __create_initial_prompt(question_sql_list: list[str], dialect_name: str) -> str:
        """
        A method to create the initial prompt.

        Parameters:
            question_sql_list (list[str]): The list of similar questions.
            dialect_name (str): The dialect name.

        Returns:
            str: The initial prompt.
        """
        initial_prompt = prompts.DEFAULT_PROMPT.format(dialect_name=dialect_name)
        return f'{initial_prompt}\n{FEW_SHOT_EXAMPLE.format(MindSQLCore.__format_qsn_sql(question_sql_list))}'

    @staticmethod
    def __format_qsn_sql(question_sql_list: list):
        """
        A method to format the question and SQL.

        Parameters:
            question_sql_list (list): The list of question and SQL.

        Returns:
            str: The formatted string.
        """
        formatted_string = "\n\n"

        for query_dict in question_sql_list:
            formatted_string += "'Question': \"{}\"\n'SQLQuery': '{}'\n\n".format(query_dict.get('Question'),
                                                                                  query_dict.get('SQLQuery'))
        return formatted_string

    @staticmethod
    def __extract_tables_and_columns_from_ddl(ddl_list: list[str]) -> dict:
        """
        Extract table names and their columns from DDL statements.
        
        Parameters:
            ddl_list (list[str]): List of DDL statements.
            
        Returns:
            dict: Dictionary mapping table names to lists of column names.
        """
        tables_and_columns = {}
        for ddl in ddl_list:
            # Handle enhanced DDL format
            if "FULL DDL:" in ddl:
                ddl_match = re.search(r'FULL DDL:\s*(.+)', ddl, re.DOTALL)
                if ddl_match:
                    ddl = ddl_match.group(1).strip()
            
            # Extract table name and columns
            table_match = re.search(r'CREATE TABLE ["`]?(\w+)["`]?\s*\((.*)\)', ddl, re.IGNORECASE | re.DOTALL)
            if table_match:
                table_name = table_match.group(1)
                columns_part = table_match.group(2)
                
                # Extract column names
                columns = []
                for col_def in columns_part.split(','):
                    col_def = col_def.strip()
                    col_match = re.match(r'["`]?(\w+)', col_def)
                    if col_match:
                        columns.append(col_match.group(1))
                
                tables_and_columns[table_name] = columns
        
        return tables_and_columns

    @staticmethod
    def __create_grounding_text(tables_and_columns: dict) -> str:
        """
        Create explicit grounding text listing allowed tables and columns.
        Enhanced with Change 4 strict instructions.
        """
        if not tables_and_columns:
            return ""

        grounding = "CRITICAL: YOU ARE ONLY ALLOWED TO USE THESE TABLES AND COLUMNS:\n\n"
        for table, columns in tables_and_columns.items():
            grounding += f"- Table '{table}': {', '.join(columns)}\n"

        grounding += "\nSTRICT RULES:\n"
        grounding += "1. If the question references ANY table or column NOT listed above, you MUST return: SELECT 'INVALID';\n"
        grounding += "2. Do NOT guess column names. Do NOT infer column names.\n"
        grounding += "3. Do NOT invent joins to tables not listed above.\n"
        grounding += "4. Only return valid SQL for the detected dialect or SELECT 'INVALID';\n"

        return grounding

    
    def __detect_primary_tables(self, question: str, connection=None) -> list[str]:
        """
        🚀 Bucket 3: Intelligent Table Extraction.
        Detects tables based on:
        1. Explicit table name mention (tokenized)
        2. Column name mention (Column-driven detection)
        """
        q = question.lower()
        primary = set()
        
        # 1. Column-driven detection (Highest Precision)
        if connection:
            column_hints = self.__extract_column_hints(question, connection)
            metadata = self.__get_real_metadata(connection)
            if metadata:
                for table, info in metadata.items():
                    table_cols = [c.lower() for c in info.get("columns", {}).keys()]
                    for hint in column_hints:
                        if hint.lower() in table_cols:
                            primary.add(table)

        # 2. Tokenized Table Name Detection
        # e.g. "plants" matches "gmiiot_plants"
        if connection and self.current_db:
            all_tables = self.database.get_table_names(connection, self.current_db)
            if all_tables is not None and not all_tables.empty:
                table_names = [t.lower() for t in all_tables['table_name'].tolist()]
                words = re.findall(r'\w+', q)
                for word in words:
                    if len(word) < 3: continue
                    for t_name in table_names:
                        if word in t_name or t_name in word:
                            primary.add(t_name)

        return list(primary)

    def __expand_table_relationships(self, primary_tables: list[str]) -> list[str]:
        """
        Expands the list of tables using the dynamic relationship graph (depth 1).
        """
        expanded = set(primary_tables)
        for table in primary_tables:
            table_lower = table.lower()
            if table_lower in self.relationship_graph:
                for related in self.relationship_graph[table_lower]:
                    expanded.add(related)
        return list(expanded)

    
    def __extract_column_hints(self, question: str, connection) -> list[str]:
        """
        Extracts potential column names from the question that exist in the database metadata.
        Used for Update 2: Column-Aware Retrieval Boost.
        """
        try:
            metadata = self.__get_real_metadata(connection)
            if not metadata:
                return []

            q_lower = question.lower()
            hints = []

            # Extract all column names across all tables
            all_cols = set()
            for info in metadata.values():
                all_cols.update(info.get("columns", {}).keys())

            for col in all_cols:
                # Use word boundaries for exact match
                if re.search(r'\b' + re.escape(col.lower()) + r'\b', q_lower):
                    hints.append(col)

            return hints
        except Exception as e:
            log.warning(f"Error extracting column hints: {e}")
            return []

    def __get_ddl_statements(self, connection: any, tables: list[str], question: str, **kwargs) -> Union[list[str], str]:
        """
        A method to get the DDL statements.
        Enhanced with Deterministic Scoring and Strict Selection.
        """
        if connection:
            # Deterministic Scoring
            scored_tables = self.__score_tables_deterministic(question, connection)
            
            # Strict Selection
            selected_tables = self.__select_tables_strictly(scored_tables, question, connection)
            
            if isinstance(selected_tables, str): # Ambiguity detected, return question
                return selected_tables

            if not selected_tables:
                # Fallback to vectorstore RAG if no tables detected via scoring
                log.info("No tables detected via scoring. Falling back to vectorstore RAG.")
                ddl_statements = self.vectorstore.retrieve_relevant_ddl(question, **kwargs)
                return ddl_statements

            # Get DDLs for selected tables
            ddl_statements = []
            for table_name in selected_tables:
                ddl = self.database.get_ddl(connection=connection, table_name=table_name)
                if ddl:
                    ddl_statements.append(ddl)
            
            # --- Mandatory Join Plan (Option G) ---
            join_plan = self.__get_mandatory_join_plan(connection, selected_tables)
            if join_plan:
                ddl_statements.append(join_plan)
            
            return ddl_statements
        
        return []

    def __get_mandatory_join_plan(self, connection, tables: list[str]) -> str:
        """
        🔒 OPTION G: Deterministic Join Planning
        Generates MANDATORY join clauses from FK metadata.
        LLM is NOT allowed to modify these joins.
        """
        if not self.relationship_graph or len(tables) < 2:
            return ""
            
        db_name = getattr(self, 'current_db', None)
        if not db_name:
            # Try to get from connection object if available
            try:
                db_name = connection.info.dbname # Postgres specific
            except:
                return ""

        fk_df = self.database.get_foreign_keys(connection, db_name)
        if fk_df is None or fk_df.empty:
            return ""
            
        plan = "\n### MANDATORY JOIN PLAN (DO NOT MODIFY) ###\n"
        plan += "You MUST use ONLY these join conditions. DO NOT invent joins.\n\n"
        
        # Filter for FKs that link tables within our selected set (case-insensitive)
        tables_lower = [t.lower() for t in tables]
        relevant_fks = fk_df[
            (fk_df['table_name'].str.lower().isin(tables_lower)) & 
            (fk_df['foreign_table_name'].str.lower().isin(tables_lower))
        ]
        
        if relevant_fks.empty:
            return ""
            
        for _, row in relevant_fks.iterrows():
            t1 = row['table_name']
            c1 = row['column_name']
            t2 = row['foreign_table_name']
            c2 = row['foreign_column_name']
            plan += f"{t1}.{c1} = {t2}.{c2}\n"
            
        plan += "\nRULES:\n"
        plan += "- You MUST use ONLY these joins\n"
        plan += "- You MUST NOT invent join conditions\n"
        plan += "- You MUST NOT skip intermediate tables\n"
        plan += "- Violating this = INVALID SQL\n"
        plan += "### END MANDATORY JOIN PLAN ###\n"
            
        return plan

    def ask_db(self, connection, question: Union[str, None] = None, table_names: list = None, visualize: bool = False,
               **kwargs) -> dict:
        """
        A method to ask the database and return the result as a dictionary.
        Enhanced with strict intent execution, precise validation, limited retries,
        and Automated RAG (Feedback Scoring + Golden Cache).
        """
        result = {}
        try:
            # 1. DATABASE METADATA Intent (Structure-level, global)
            if self.__is_database_metadata_intent(question):
                result["response"] = self.__handle_database_metadata(question, connection)
                return result

            # 2. SCHEMA_LOOKUP Intent (Table/column facts)
            if self.__is_schema_lookup_intent(question):
                result["response"] = self.__handle_schema_lookup(question, connection, **kwargs)
                return result

            # 3. ANALYSIS Intent (Why/explain/relationship)
            if self.__is_analysis_intent(question):
                result["response"] = self.__handle_analysis_query(question, connection, **kwargs)
                return result

            # 4. DATA RETRIEVAL - SQL GENERATION (with cache and retry - Updates 1, 3)
            # Check Golden Cache first
            sql = self.golden_cache.get_cached_sql(question)

            if sql:
                log.info(f"Golden Cache HIT for question: {question}")
                result["sql"] = sql
                # Use cached SQL directly for execution (assuming it was verified once)
                ddl_list = self.__get_ddl_statements(connection, table_names, question, **kwargs)
            else:
                max_retries = 3
                current_retry = 0
                
                # Fetch DDLs/Ambiguity Check once before starting retries
                ddl_list = self.__get_ddl_statements(connection, table_names, question, **kwargs)
                if isinstance(ddl_list, str): # Ambiguity detected
                    result["response"] = ddl_list
                    return result

                current_validation_error = None
                while current_retry < max_retries:
                    sql = self.create_database_query(
                        question=question, 
                        connection=connection, 
                        tables=table_names, 
                        validation_error=current_validation_error,
                        **kwargs
                    )
                    result["sql"] = sql

                    if not sql or "I cannot find" in sql or "invalid" in sql.lower():
                        current_retry += 1
                        current_validation_error = "LLM returned INVALID or could not find tables."
                        log.info(f"Invalid SQL generated, retry {current_retry}/{max_retries}")
                        continue

                    # 1. Column Validation
                    is_valid, err_msg = self.__validate_sql_columns(sql, ddl_list)
                    if not is_valid:
                        current_retry += 1
                        current_validation_error = f"Validation Error: {err_msg}"
                        log.info(f"SQL Validation failed: {err_msg}, retry {current_retry}/{max_retries}")
                        continue
                    
                    # 2. FK-Only Join Validation (Option G)
                    is_valid, err_msg = self.__validate_sql_joins(sql, connection)
                    if not is_valid:
                        current_retry += 1
                        current_validation_error = f"FK Validation Error: {err_msg}"
                        log.info(f"FK Validation failed: {err_msg}, retry {current_retry}/{max_retries}")
                        continue
                    
                    # 3. Semantic Logic Validation (Option H)
                    is_valid, err_msg = self.__validate_semantic_logic(sql, connection)
                    if not is_valid:
                        current_retry += 1
                        current_validation_error = f"Semantic Error: {err_msg}"
                        log.info(f"Semantic Validation failed: {err_msg}, retry {current_retry}/{max_retries}")
                        continue
                    
                    # If all validations pass
                    break
                
                if current_retry >= max_retries:
                    # Switch to Explanation Mode
                    result["response"] = self.__handle_explanation_mode(question, connection, ddl_list)
                    
                    # Log failure
                    tables = []
                    for ddl in ddl_list:
                        match = re.search(r'TABLE NAME:\s*(\w+)', ddl)
                        if match: tables.append(match.group(1))
                    self.feedback_logger.log_feedback(question, tables, "failure")
                    
                    return result

            # 4. EXECUTION (Shared for both Cache HIT and fresh SUCCESS)
            df = self.database.execute_sql(connection, sql)

            if df is None or df.empty:
                response = "No data exists for your query."
            else:
                # Stringify DataFrame to avoid formatting/truncation issues in prompt
                df_str = df.to_string(index=False)
                response = self.llm.invoke(prompts.FINAL_RESPONSE_PROMPT.format(context_df=df_str, user_query=question))

            result.update({
                "sql_result": df,
                "response": response,
                "chart": self.visualize(question, df, visualize)
            })

            # Automated RAG Feedback & Cache Storage
            tables = []
            for ddl in ddl_list:
                match = re.search(r'TABLE NAME:\s*(\w+)', ddl)
                if match: tables.append(match.group(1))
            self.feedback_logger.log_feedback(question, tables, "success")
            self.golden_cache.store_query(question, sql)

            log.info(f"Query: {question} \nLLM Response: {result.get('response')}")
            return result

        except Exception as e:
            log.warning(f"An unexpected error occurred: {e}")
            result["error"] = e
            result["response"] = f"An error occurred: {str(e)}"
            return result


    def __is_database_metadata_intent(self, question: str) -> bool:
        """
        Detects if the user is asking for DATABASE METADATA (structure-level, global).
        Examples: "list all tables", "how many tables", "what tables exist"
        """
        metadata_keywords = [
            "list all tables",
            "list tables",
            "show tables",
            "show all tables",
            "how many tables",
            "what tables",
            "which tables exist",
            "get all tables",
            "all table names"
        ]
        q_lower = question.lower().strip()
        return any(keyword in q_lower for keyword in metadata_keywords)

    def __handle_database_metadata(self, question: str, connection) -> str:
        """
        Handles DATABASE METADATA questions using real database schema.
        NO SQL, NO LLM - direct metadata extraction only.
        """
        try:
            metadata = self.__get_real_metadata(connection)
            
            # 🔄 Integration Fix: Load synthetic fallback if real metadata extraction fails
            if not metadata:
                log.info("Direct metadata extraction failed. Falling back to synthetic_schemas.json.")
                current_dir = os.path.dirname(os.path.abspath(__file__))
                fallback_path = os.path.join(current_dir, "synthetic_schemas.json")
                if os.path.exists(fallback_path):
                    with open(fallback_path, 'r') as f:
                        metadata = json.load(f)
            
            if not metadata:
                return "Could not retrieve database metadata from real or synthetic sources."

            q_lower = question.lower()
            table_names = list(metadata.keys())

            # "list all tables" or "show tables"
            if any(kw in q_lower for kw in ["list", "show", "what tables", "which tables"]):
                return f"The database contains {len(table_names)} tables: {', '.join(sorted(table_names))}"

            # "how many tables"
            if "how many" in q_lower:
                return f"The database contains {len(table_names)} tables."

            # Default
            return f"Available tables: {', '.join(sorted(table_names))}"

        except Exception as e:
            log.error(f"Error in database metadata lookup: {e}")
            return f"An error occurred while retrieving database metadata: {str(e)}"

    def __is_analysis_intent(self, question: str) -> bool:
        """
        Detects if the user is asking for an analysis/explanation.
        """
        analysis_keywords = [
            "explain", "relationship", "trend", "pattern", "why",
            "how does", "what is the difference", "analysis", "compare",
            "what is", "about", "details", "information", "tell me about"
        ]
        q_lower = question.lower()
        return any(keyword in q_lower for keyword in analysis_keywords)

    def __handle_analysis_query(self, question: str, connection, **kwargs) -> str:
        """
        Handles questions requiring natural language analysis/explanation.
        """
        # Retrieve context
        kwargs.get("tables", [])
        ddl_list = self.vectorstore.retrieve_relevant_ddl(question, **kwargs)
        doc_list = self.vectorstore.retrieve_relevant_documentation(question, **kwargs)

        log.info(f"Analysis Intent - Question: {question}")
        log.info(f"Analysis Intent - Retrieved DDLs: {len(ddl_list)} table(s)")
        for ddl in ddl_list:
            match = re.search(r'TABLE NAME: ([\w]+)', ddl)
            if match:
                log.info(f"  - Context Table: {match.group(1)}")

        schema_context = "\n".join(ddl_list) if ddl_list else ""
        docs_context = "\n".join(doc_list) if doc_list else ""

        prompt = f"{prompts.ANALYSIS_PROMPT}\n\nSCHEMA:\n{schema_context}\n\nDOCS:\n{docs_context}\n\nQUESTION: {question}"

        return self.llm.invoke(prompt)

    def __is_schema_lookup_intent(self, question: str) -> bool:
        """
        Detects if the user is asking for a SCHEMA_LOOKUP (factual database structure).
        Only triggers for questions about database structure, NOT data queries.
        """
        schema_keywords = [
            "which table",
            "which tables",
            "where is",
            "stored",
            "contains",
            # "belong to" removed - too broad, catches data queries
            "domain",
            "which column",
            "columns",
            "schema",
            "table has",
            "tables have",
            "zero rows",
            "empty table",
            "no rows",
            "row count",
            "how many rows",
            "what columns",
            "list columns"
        ]
        q_lower = question.lower()
        
        # Additional check: if asking "which X" where X is a table name, it's likely a data query
        # Example: "which machines" is data, but "which table has column X" is schema
        if q_lower.startswith("which ") and not any(kw in q_lower for kw in ["table", "column"]):
            return False
            
        return any(keyword in q_lower for keyword in schema_keywords)


    def __handle_schema_lookup(self, question: str, connection, **kwargs) -> str:
        """
        Handles SCHEMA_LOOKUP questions using factual lookups from the REAL database.
        Enhanced for precision: No fuzzy matching, exact column lookups only.
        """
        try:
            metadata = self.__get_real_metadata(connection)
            if not metadata:
                return "Could not retrieve schema metadata from the database."

            q_lower = question.lower()

            # --- Specific Analytics (Rows, Columns, etc.) ---
            if "most rows" in q_lower:
                table_counts = {t: info.get("metadata", {}).get("row_count") for t, info in metadata.items()
                               if info.get("metadata", {}).get("row_count") is not None}
                if table_counts:
                    max_table = max(table_counts, key=table_counts.get)
                    return f"The table with the most rows is '{max_table}' (Row Count: {table_counts[max_table]})."

            if "how many rows" in q_lower:
                for table_name in metadata.keys():
                    if table_name.lower() in q_lower:
                        row_count = metadata[table_name].get("metadata", {}).get("row_count")
                        return f"The table '{table_name}' has {row_count} rows."

            if "what columns" in q_lower or "list all columns" in q_lower:
                for table_name in metadata.keys():
                    if table_name.lower() in q_lower:
                        cols = list(metadata[table_name].get("columns", {}).keys())
                        return f"The table '{table_name}' has the following columns: {', '.join(cols)}."


            # --- Zero Rows / Empty Table ---
            if any(kw in q_lower for kw in ["zero rows", "empty table", "no rows"]):
                empty_tables = []
                for table_name, info in metadata.items():
                    row_count = info.get("metadata", {}).get("row_count")
                    if row_count == 0 or row_count == "0":
                        empty_tables.append(table_name)
                
                if empty_tables:
                    return f"Tables with zero rows: {', '.join(empty_tables)}"
                else:
                    return "All tables contain data (no empty tables found)."

            # --- Precise Column Lookup (Change 5) ---
            # Use word boundaries to find column mentioned in question
            all_cols = set()
            for info in metadata.values():
                all_cols.update(info.get("columns", {}).keys())

            found_cols = []
            for col in all_cols:
                if re.search(r'\b' + re.escape(col.lower()) + r'\b', q_lower):
                    found_cols.append(col)

            if found_cols:
                response = "Based on the schema metadata:\n"
                for target_col in found_cols:
                    tables_with_col = [t for t, info in metadata.items() if target_col in info.get("columns", {})]
                    if len(tables_with_col) == 1:
                        response += f"- The '{target_col}' column is stored in the '{tables_with_col[0]}' table.\n"
                    else:
                        response += f"- The '{target_col}' column is found in multiple tables: {', '.join(tables_with_col)}.\n"
                return response


            # --- Fallback (No Guessing) ---
            return (
                "This is a schema-level question.\n"
                "I couldn't find an exact match for a column or table in your question.\n"
                f"Available tables: {', '.join(metadata.keys())}"
            )

        except Exception as e:
            log.error(f"Error in schema lookup: {e}")
            return f"An error occurred while looking up schema information: {str(e)}"
    def __handle_explanation_mode(self, question: str, connection, ddl_list: list[str]) -> str:
        """
        Factual explanation when SQL generation fails.
        """
        metadata = self.__get_real_metadata(connection)
        tables_involved = []
        for ddl in ddl_list:
            match = re.search(r'TABLE NAME:\s*(\w+)', ddl)
            if match:
                tables_involved.append(match.group(1))
        
        explanation = "I was unable to generate a valid SQL query for your request even after 3 attempts. Here are the facts from the schema:\n\n"
        
        explanation += "### TABLES & DATA:\n"
        for table in tables_involved:
            row_count = metadata.get(table, {}).get("metadata", {}).get("row_count", "unknown")
            explanation += f"- Table '{table}' has {row_count} rows.\n"
            
        if self.relationship_graph:
            explanation += "\n### RELATIONSHIP MAP (How tables connect):\n"
            found_rel = False
            for t_idx, t1 in enumerate(tables_involved):
                t1_lower = t1.lower()
                neighbors = self.relationship_graph.get(t1_lower, [])
                for t2 in tables_involved[t_idx+1:]:
                    t2_lower = t2.lower()
                    if t2_lower in neighbors:
                        explanation += f"- '{t1}' can join directly with '{t2}'\n"
                        found_rel = True
                    else:
                        # Check for indirect path
                        path = self.__find_shortest_path(t1_lower, t2_lower)
                        if path:
                            explanation += f"- '{t1}' connects to '{t2}' via path: {' -> '.join(path)}\n"
                            found_rel = True
            
            if not found_rel:
                explanation += "- No direct or indirect relationships were detected between these specific tables.\n"
                explanation += "\nTIP: Queries spanning unrelated tables are not possible. Try asking about tables that are connected."

        return explanation


    def __score_tables_deterministic(self, question: str, connection) -> dict:
        """
        Scores tables based on deterministic rules:
        - +10: Exact column name match
        - +8: Exact table name match
        - +6: Tokenized table name match
        - +5: Column appears in WHERE/filter intent
        - +4: FK relationship to a higher-scoring table
        - +2: Partial semantic relevance
        - -inf: Block if not in schema
        """
        metadata = self.__get_real_metadata(connection)
        if not metadata:
            return {}

        scores = {table: 0 for table in metadata.keys()}
        q_lower = question.lower()
        words = re.findall(r'\w+', q_lower)
        
        filter_keywords = {"status", "date", "active", "count", "type", "state", "id", "name"}

        # 1. Column & Table scoring
        for table, info in metadata.items():
            table_lower = table.lower()
            cols = info.get("columns", {})
            
            # +15 Exact Table Name (Boosted from +8 to favor explicit table mentions)
            if table_lower in q_lower:
                scores[table] += 15
            
            # +6 Tokenized table match
            for word in words:
                if len(word) >= 3 and (word in table_lower or table_lower in word) and table_lower not in q_lower:
                    scores[table] += 6

            for col in cols.keys():
                col_lower = col.lower()
                pattern = r'\b' + re.escape(col_lower) + r'\b'
                
                # +10 Exact Column Name
                if re.search(pattern, q_lower):
                    scores[table] += 10
                    
                    # +5 Filter Intent
                    if col_lower in filter_keywords:
                        scores[table] += 5

        # 2. Vector Search Scoring (+5 boost)
        # Combine string detection with semantic search
        try:
            # Search relevant DDLs (identifies tables via semantic schema match)
            vector_ddls = self.vectorstore.retrieve_relevant_ddl(question, k=3)
            for ddl in vector_ddls:
                match = re.search(r'TABLE NAME:\s*(\w+)', ddl)
                if not match: match = re.search(r'CREATE TABLE "?([\w]+)"?', ddl, re.IGNORECASE)
                if match:
                    table_name = match.group(1)
                    if table_name in scores:
                        scores[table_name] += 5

            # Search Documentation (identifies tables via relationships/notes)
            docs = self.vectorstore.retrieve_relevant_documentation(question, k=5)
            for doc in docs:
                # Extract any table names mentioned in documentation (handle optional quotes)
                # Matches: Table machines, Table 'machines', Table "machines"
                found_in_doc = re.findall(r'table\s+[\'"]?([a-zA-Z0-9_]+)[\'"]?', doc.lower())
                for t in found_in_doc:
                    # Find actual case-sensitive table name
                    for real_table in scores.keys():
                        if real_table.lower() == t:
                            scores[real_table] += 10 # Increase boost for semantic docs
        except Exception as e:
            log.warning(f"Vector search scoring failed: {e}")

        # 3. Relationship Scoring (+4)
        # Apply FK boost to neighbors of high-scoring tables
        if self.relationship_graph:
            high_scoring_tables = [t for t, s in scores.items() if s >= 8]
            for table in high_scoring_tables:
                table_lower = table.lower()
                if table_lower in self.relationship_graph:
                    for related in self.relationship_graph[table_lower]:
                        # Find the actual case-sensitive table name
                        for real_table in scores.keys():
                            if real_table.lower() == related:
                                if scores[real_table] < scores[table]: # Only boost if lower
                                    scores[real_table] += 4

        return scores


    def __find_shortest_path(self, start: str, end: str) -> list[str]:
        """
        BFS to find shortest path between two tables in relationship graph.
        """
        if start == end: return [start]
        queue = [[start]]
        visited = {start}
        
        while queue:
            path = queue.pop(0)
            node = path[-1]
            for neighbor in self.relationship_graph.get(node, []):
                if neighbor == end:
                    return path + [end]
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])
        return []

    def __select_tables_strictly(self, scored_tables: dict, question: str, connection) -> Union[list[str], str]:
        """
        Strict Selection Rules:
        - Primary table (highest score)
        - Secondary (score > 10 OR required for join)
        - Intermediate (FK required)
        - Detect Ambiguity: If multiple high-scoring tables have same score and same columns.
        """
        if not scored_tables: return []
        
        sorted_tables = sorted(scored_tables.items(), key=lambda x: x[1], reverse=True)
        max_score = sorted_tables[0][1]
        
        if max_score == 0:
            return []

        # Ambiguity Check
        top_tables = [t for t, s in sorted_tables if s == max_score and s > 0]
        if len(top_tables) > 1:
            metadata = self.__get_real_metadata(connection)
            q_lower = question.lower()
            
            # Find the ambiguous columns
            ambiguous_cols = []
            all_cols = set()
            for t in top_tables:
                all_cols.update(metadata.get(t, {}).get("columns", {}).keys())
            
            for col in all_cols:
                if re.search(r'\b' + re.escape(col.lower()) + r'\b', q_lower):
                    # Check if this column exists in multiple high-scoring tables
                    tables_with_col = [t for t in top_tables if col in metadata.get(t, {}).get("columns", {})]
                    if len(tables_with_col) > 1:
                        ambiguous_cols.append((col, tables_with_col))

            if ambiguous_cols:
                col, tables = ambiguous_cols[0] # Pick first ambiguity
                return f"The column '{col}' exists in multiple tables: {', '.join(tables)}. Which one do you mean?"

        # Selection
        # 1. Start with the top scoring table (above 0)
        if not sorted_tables or sorted_tables[0][1] <= 0:
            return []
            
        selected = {sorted_tables[0][0]}
        
        # 2. Add high-scoring secondary tables (score >= 10 for broader coverage)
        # Limit to top 10 high-scoring tables to balance coverage vs context
        secondary_count = 0
        for table, score in sorted_tables[1:]:
            if score >= 10 and secondary_count < 10:
                selected.add(table)
                secondary_count += 1

        # Add Intermediates to ensure connectivity
        final_selection = set(selected)
        if len(selected) > 1 and self.relationship_graph:
            # We want to make sure all selected tables can reach the primary table
            primary = sorted_tables[0][0].lower()
            for other in selected:
                if other.lower() == primary: continue
                path = self.__find_shortest_path(primary, other.lower())
                if path:
                    # Map lowercase paths back to case-sensitive table names
                    for p in path:
                        for real_table in scored_tables.keys():
                            if real_table.lower() == p:
                                final_selection.add(real_table)
        
        # Final safety cap: If we still have too many tables, only take top 10 and their join paths
        if len(final_selection) > 12:
            log.warning(f"Too many tables ({len(final_selection)}) selected. Capping to top 5 + paths.")
            top_5_orig = [t for t, s in sorted_tables[:5]]
            final_selection = set(top_5_orig)
            primary = top_5_orig[0].lower()
            for other in top_5_orig[1:]:
                path = self.__find_shortest_path(primary, other.lower())
                for p in path:
                    for real_table in scored_tables.keys():
                        if real_table.lower() == p:
                            final_selection.add(real_table)

        return list(final_selection)

    def __get_real_metadata(self, connection) -> dict:
        """
        Fetches real schema metadata from the connected database.
        """
        metadata = {}
        try:
            dialect = self.database.get_dialect().lower()

            if dialect == "postgres":
                # Get tables and columns
                query = """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position;
                """
                df = self.database.execute_sql(connection, query)

                # Get approximate row counts
                stats_query = """
                SELECT relname AS table_name, n_live_tup AS row_count
                FROM pg_stat_user_tables;
                """
                df_stats = self.database.execute_sql(connection, stats_query)
                stats_map = {}
                if df_stats is not None and not df_stats.empty:
                    stats_map = dict(zip(df_stats['table_name'], df_stats['row_count']))

                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        table = row['table_name']
                        col = row['column_name']
                        dtype = row['data_type']

                        if table not in metadata:
                            metadata[table] = {
                                "columns": {},
                                "metadata": {"row_count": stats_map.get(table, 0)}
                            }
                        metadata[table]["columns"][col] = {"type": dtype}

            elif dialect in ["sqlite", "sqlite3"]:
                query = "SELECT name FROM sqlite_master WHERE type='table';"
                df_tables = self.database.execute_sql(connection, query)
                if df_tables is not None and not df_tables.empty:
                    for table in df_tables['name']:
                        col_query = f"PRAGMA table_info({table});"
                        df_cols = self.database.execute_sql(connection, col_query)
                        
                        # Get actual row count
                        row_count = 0
                        try:
                            # Schema-qualify the table name
                            schema = getattr(self.database, 'current_schema', 'public')
                            count_query = f'SELECT COUNT(*) as cnt FROM "{schema}"."{table}";'
                            df_count = self.database.execute_sql(connection, count_query)
                            if df_count is not None and not df_count.empty:
                                row_count = int(df_count['cnt'].iloc[0])
                        except Exception:
                            row_count = 0
                        
                        metadata[table] = {"columns": {}, "metadata": {"row_count": row_count}}
                        for _, col in df_cols.iterrows():
                            metadata[table]["columns"][col['name']] = {"type": col['type']}

        except Exception as e:
            log.error(f"Failed to fetch real metadata: {e}")

        return metadata

    def __validate_sql_columns(self, sql: str, ddl_list: list[str]) -> tuple[bool, str]:
        """
        Validates that all columns in the SQL query exist in the provided DDLs
        and belong to the tables actively present in the query.
        Returns (is_valid, error_message).
        """
        # 1. Remove string literals to avoid false positives
        clean_sql = re.sub(r"'.*?'", " ", sql)
        
        # 2. Extract allowed schema: {table_name: {column_names}}
        allowed_schema = {}
        for ddl in ddl_list:
            if "FULL DDL:" in ddl:
                ddl_match = re.search(r'FULL DDL:\s*(.+)', ddl, re.DOTALL)
                if ddl_match:
                    ddl = ddl_match.group(1).strip()
            
            table_match = re.search(r'CREATE TABLE ["`]?(\w+)["`]?\s*\((.*)\)', ddl, re.IGNORECASE | re.DOTALL)
            if table_match:
                t_name = table_match.group(1).lower()
                cols = []
                columns_part = table_match.group(2)
                for col_def in columns_part.split(','):
                    col_match = re.match(r'["`]?(\w+)', col_def.strip())
                    if col_match:
                        cols.append(col_match.group(1).lower())
                allowed_schema[t_name] = set(cols)

        # 3. Identify tables used in the query (FROM and JOIN)
        tables_in_query = set()
        for match in re.finditer(r'(?:FROM|JOIN)\s+["`]?(\w+)["`]?', clean_sql, re.IGNORECASE):
            tables_in_query.add(match.group(1).lower())

        # 4. Identify aliases (AS and implicit) and table names used as prefixes
        query_aliases = set()
        alias_to_table = {}
        
        # Explicit AS aliases and implicit ones in FROM/JOIN
        for match in re.finditer(r'(?:FROM|JOIN)\s+["`]?(\w+)["`]?\s+(?:AS\s+)?["`]?(\w+)["`]?', clean_sql, re.IGNORECASE):
            t_name = match.group(1).lower()
            alias = match.group(2).lower()
            if alias not in ["join", "on", "where", "group", "order", "limit", "as", "by", "having", "left", "right", "inner", "outer"]:
                query_aliases.add(alias)
                alias_to_table[alias] = t_name
        
        # Add the table names themselves as aliases (self-reference)
        for t in tables_in_query:
            query_aliases.add(t)
            alias_to_table[t] = t

        # 5. Scoped Validation: Ensure tokens belong to active tables
        active_allowed_columns = set()
        active_allowed_tables = set()
        for t in tables_in_query:
            if t in allowed_schema:
                active_allowed_columns.update(allowed_schema[t])
                active_allowed_tables.add(t)

        # Match all words (potential tables or columns)
        all_words = re.findall(r'(\w+)', clean_sql)
        keywords = {
            'select', 'from', 'where', 'limit', 'join', 'on', 'group', 'by', 'order', 'desc', 'asc', 
            'and', 'or', 'in', 'is', 'not', 'null', 'count', 'sum', 'avg', 'min', 'max', 'as', 
            'distinct', 'inner', 'left', 'right', 'outer', 'between', 'like', 'any', 'all', 'exists', 'values'
        }
        
        for word in all_words:
            word = word.lower()
            if word.isdigit() or word in keywords:
                continue
            if len(word) <= 1:
                continue
            if word[0] == 't' and len(word) > 1 and word[1:].isdigit():
                continue
            if word in query_aliases:
                continue

            if word not in active_allowed_tables and word not in active_allowed_columns:
                if word not in allowed_schema:
                    err = f"Identifier '{word}' not found in the provided schema for tables {list(active_allowed_tables)}."
                    log.warning(f"Validation FAILED: {err}")
                    return False, err
            
            # 5a. Ambiguity Check: If word is a column, ensure it's NOT in multiple tables OR it IS scoped
            if word in active_allowed_columns and word not in active_allowed_tables:
                # Check if this word appears as an unscoped column in the SQL
                # (Simple check: is it preceded by a dot?)
                is_scoped = re.search(r'\.\s*' + re.escape(word), clean_sql, re.IGNORECASE)
                if not is_scoped:
                    # How many tables in the query have this column?
                    tables_with_col = [t for t in active_allowed_tables if word in allowed_schema.get(t, set())]
                    if len(tables_with_col) > 1:
                        err = f"Ambiguous column reference '{word}'. It exists in multiple tables: {tables_with_col}. You MUST qualify it with a table alias (e.g., alias.{word})."
                        log.warning(f"Validation FAILED: {err}")
                        return False, err

        # 6. Precise scoped check for table.column
        scoped_matches = re.findall(r'["`]?(\w+)["`]?\.["`]?(\w+)["`]?', clean_sql)
        for alias, c in scoped_matches:
            alias, c = alias.lower(), c.lower()
            if alias.isdigit():
                continue
            real_table = alias_to_table.get(alias)
            if not real_table:
                # If prefix is not a known alias, check if 'c' exists in ANY table in query
                if not any(c in allowed_schema.get(t_q, set()) for t_q in tables_in_query):
                    err = f"Column '{c}' not found in any of the tables in the query scope: {tables_in_query}"
                    log.warning(f"Validation FAILED: {err}")
                    return False, err
                continue
            
            if real_table in allowed_schema:
                if c not in allowed_schema[real_table]:
                    err = f"Column '{c}' does not exist in table '{real_table}'."
                    log.warning(f"Validation FAILED: {err}")
                    return False, err

        # 7. Join Key Validation
        join_pattern = r'JOIN\s+[\w"`]+\s+(?:AS\s+)?(\w+)\s+ON\s+(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)'
        join_matches = re.findall(join_pattern, clean_sql, re.IGNORECASE | re.MULTILINE)
        
        if join_matches and self.relationship_graph:
            for alias_new, alias1, col1, alias2, col2 in join_matches:
                t1 = alias_to_table.get(alias1.lower())
                t2 = alias_to_table.get(alias2.lower())
                
                if t1 and t2 and t1 != t2:
                    is_neighbor = t2 in self.relationship_graph.get(t1, []) or t1 in self.relationship_graph.get(t2, [])
                    if not is_neighbor:
                        err = f"Illegal Join attempt between '{t1}' and '{t2}'. These tables have no direct relationship in the schema. You MUST use intermediate join tables."
                        log.warning(f"Validation FAILED: {err}")
                        return False, err

        return True, ""

    def __validate_sql_joins(self, sql: str, connection) -> tuple[bool, str]:
        """
        🔒 OPTION G: Enforces that every JOIN uses actual FK relationships.
        Rejects hallucinated joins before execution.
        """
        db_name = getattr(self, 'current_db', None)
        if not db_name:
            try:
                db_name = connection.info.dbname
            except:
                return True, ""  # Skip validation if no DB context
        
        fk_df = self.database.get_foreign_keys(connection, db_name)
        if fk_df is None or fk_df.empty:
            return True, ""  # No FKs to validate against
        
        # Build FK lookup: (table1_lower, col1_lower, table2_lower, col2_lower)
        valid_joins = set()
        for _, row in fk_df.iterrows():
            t1 = row['table_name'].lower()
            c1 = row['column_name'].lower()
            t2 = row['foreign_table_name'].lower()
            c2 = row['foreign_column_name'].lower()
            valid_joins.add((t1, c1, t2, c2))
            valid_joins.add((t2, c2, t1, c1))  # Bidirectional
        
        # Extract JOIN ... ON clauses from SQL
        join_pattern = r'JOIN\s+["`]?(\w+)["`]?(?:\s+AS\s+["`]?(\w+)["`]?)?\s+ON\s+([\w.]+)\s*=\s*([\w.]+)'
        clean_sql = re.sub(r"'.*?'", " ", sql)  # Remove string literals
        
        for match in re.finditer(join_pattern, clean_sql, re.IGNORECASE):
            left_expr = match.group(3).lower()
            right_expr = match.group(4).lower()
            
            # Parse table.column from expressions
            def parse_col(expr):
                parts = expr.split('.')
                if len(parts) == 2:
                    return parts[0].strip(), parts[1].strip()
                return None, None
            
            t1, c1 = parse_col(left_expr)
            t2, c2 = parse_col(right_expr)
            
            if not all([t1, c1, t2, c2]):
                continue  # Skip malformed joins
            
            # Check if this join exists in FK metadata
            if (t1, c1, t2, c2) not in valid_joins:
                err = f"Illegal join: {t1}.{c1} = {t2}.{c2}. This is not a valid FK relationship."
                log.warning(f"FK Validation FAILED: {err}")
                return False, err
        
        return True, ""

    def __validate_semantic_logic(self, sql: str, connection) -> tuple[bool, str]:
        """
        🛡️ OPTION H: Semantic guardrails to catch logical errors.
        - Reject LIKE on INTEGER/ID columns
        - Reject string filters on TIMESTAMP columns
        """
        metadata = self.__get_real_metadata(connection)
        
        # Build type map: {table.column: type}
        type_map = {}
        for table, info in metadata.items():
            for col, col_info in info.get("columns", {}).items():
                type_map[f"{table.lower()}.{col.lower()}"] = col_info.get("type", "").lower()
        
        clean_sql = sql.lower()
        
        # Rule 1: Reject LIKE on INTEGER/ID columns
        like_pattern = r'(\w+\.\w+)\s+(?:I)?LIKE'
        for match in re.finditer(like_pattern, clean_sql, re.IGNORECASE):
            col_ref = match.group(1).lower()
            col_type = type_map.get(col_ref, "")
            
            if any(t in col_type for t in ['integer', 'int', 'bigint', 'serial']):
                err = f"Semantic Error: Cannot use LIKE on integer column '{col_ref}'"
                log.warning(f"Semantic Validation FAILED: {err}")
                return False, err
            
            # Also check if column name suggests it's an ID
            if col_ref.endswith('_id') or col_ref.endswith('.id'):
                err = f"Semantic Error: Cannot use LIKE on ID column '{col_ref}'"
                log.warning(f"Semantic Validation FAILED: {err}")
                return False, err
        
        # Rule 2: Reject string filters on TIMESTAMP columns
        timestamp_pattern = r'(\w+\.\w+)\s*=\s*\'([^\']+)\''
        for match in re.finditer(timestamp_pattern, sql, re.IGNORECASE):
            col_ref = match.group(1).lower()
            value = match.group(2)
            col_type = type_map.get(col_ref, "")
            
            if 'timestamp' in col_type or 'date' in col_type:
                # Check if value looks like a name (contains letters beyond date chars)
                if any(c.isalpha() and c not in ['t', 'z', '-', ':'] for c in value.lower()):
                    err = f"Semantic Error: Cannot filter timestamp column '{col_ref}' with string value '{value}'"
                    log.warning(f"Semantic Validation FAILED: {err}")
                    return False, err
        
        return True, ""

    def index(self, question: str = None, sql: str = None, ddl: str = None, documentation: str = None,
              bulk: bool = False, path: str = None) -> str:
        """
        A method to add a question and SQL pair to the vectorstore.

        Parameters:
            question (str): The question to be added to the vectorstore.
            sql (str): The SQL to be added to the vectorstore.
            ddl (str): The DDL to be added to the vectorstore.
            documentation (str): The documentation to be added to the vectorstore.
            bulk (bool): Whether to add bulk data.
            path (str): The path to the JSON file.

        Returns:
            str: A message confirming the successful addition of the question and SQL pair.
        """
        if bulk and path:
            json_data = load_json_to_dict(path)
            if json_data:
                for item in json_data:
                    if 'Question' in item and 'SQLQuery' in item:
                        self.vectorstore.index_question_sql(question=item.get('Question'), sql=item.get('SQLQuery'))
            else:
                raise Exception(NO_DATA_FOUND_IN_JSON_CONSTANT.format(path))
            log.info(BULK_DATA_SUCCESS_MESSAGE_CONSTANT)

        if path and not bulk:
            raise ValueError(BULK_FALSE_ERROR)

        if question and not sql:
            raise ValueError(SQL_NOT_PROVIDED_CONSTANT)

        if question and sql:
            log.info(ADD_QUESTION_SQL_MESSAGE_CONSTANT)
            return self.vectorstore.index_question_sql(question=question, sql=sql)

        if documentation:
            log.info(ADD_DOCS_MESSAGE_CONSTANT)
            return self.vectorstore.index_documentation(documentation)

        if ddl:
            log.info(ADD_DDL_MESSAGE_CONSTANT)
            return self.vectorstore.index_ddl(ddl)

        return ""

    @staticmethod
    def __extract_plotly_code(markdown_string: str) -> str:
        """
        A method to extract the plotly code from the markdown string.

        Parameters:
            markdown_string (str): The markdown string.

        Returns:
            str: The extracted plotly code.
        """
        pattern = r"```[\w\s]*python\n([\s\S]*?)```|```([\s\S]*?)```"

        matches = re.findall(pattern, markdown_string, re.IGNORECASE)
        python_code = [match[0] or match[1] for match in matches]

        if not python_code:
            return markdown_string

        extracted_code = ''.join(python_code)
        sanitized_code = extracted_code.replace("fig.show()", "")
        return sanitized_code

    @staticmethod
    def __execute_plotly_code(plotly_code: str, data: pd.DataFrame) -> Optional[go.Figure]:
        """
        A method to execute the plotly code.

        Parameters:
            plotly_code (str): The plotly code.
            data (pd.DataFrame): The data.

        Returns:
            Optional[go.Figure]: The chart.
        """
        _locals = {"pd": pd, "go": go, "px": px, "df": data, "make_subplots": make_subplots}
        exec(plotly_code, globals(), _locals)
        return _locals.get("chart", None)

    def visualize(self, query: str, data: pd.DataFrame, visualize: bool = False) -> Optional[go.Figure]:
        """
        A method to visualize the data.

        Parameters:
            query (str): The query.
            data (pd.DataFrame): The data.
            visualize (bool): Whether to visualize the data.

        Returns:
            Optional[go.Figure]: The chart.
        """
        if visualize and not data.empty:
            try:
                if len(data.columns) == 1:
                    log.warning("Cannot create a chart for a one-dimensional DataFrame with only one column.")
                    return None
                prompt = PLOTLY_PROMPT.format(query=query, df=data)
                result = self.llm.invoke(prompt)
                plotly_code = self.__extract_plotly_code(result)
                fig = self.__execute_plotly_code(plotly_code, data)

                try:
                    if 'IPython' in sys.modules:
                        # Running in a Jupyter environment
                        display = __import__("IPython.display", fromlist=["display"]).display
                        image = __import__("IPython.display", fromlist=["Image"]).Image
                        img_bytes = fig.to_image(format="png", scale=2)
                        display(image(img_bytes))
                except Exception as e:
                    log.warning(f"Unable to display the Plotly figure: {e}")

                return fig
            except Exception as e:
                log.warning(f"An unexpected error occurred while generating chart: {e}")
                return None
        return None


    def index_all_ddls(self, connection, db_name):
        """
        Indexes all Data Definition Language (DDL) statements from the specified database into the vectorstore.
        
        Enhanced with descriptive metadata and AUTO-DISCOVERED relationships.
        """
        self.database.validate_connection(connection)
        self.current_db = db_name # Store current DB context
        
        # 1. First, Discover Relationships (Bucket ❌ Elimination)
        log.info(f"Discovering relationships for database: {db_name}")
        fk_df = self.database.get_foreign_keys(connection, db_name)
        self.relationship_graph = {}
        
        if fk_df is not None and not fk_df.empty:
            for _, row in fk_df.iterrows():
                t1 = row['table_name'].lower()
                t2 = row['foreign_table_name'].lower()
                
                if t1 not in self.relationship_graph: self.relationship_graph[t1] = set()
                if t2 not in self.relationship_graph: self.relationship_graph[t2] = set()
                
                self.relationship_graph[t1].add(t2)
                self.relationship_graph[t2].add(t1)
            
            log.info(f"Discovered {len(fk_df)} relationship(s) across {len(self.relationship_graph)} table(s).")
        
        # 2. Index DDLs
        ddls = self.database.get_all_ddls(connection=connection, database=db_name)
        for ind in ddls.index:
            table_name = ddls["Table"][ind]
            raw_ddl = ddls["DDL"][ind]

            # Enhance the text being embedded for better semantic matching
            columns = re.findall(r'\"?(\w+)\"?\s+\w+', raw_ddl)
            col_text = ", ".join(columns[:10])

            enhanced_ddl = (
                f"TABLE NAME: {table_name}\n"
                f"COLUMNS: {col_text}\n"
                f"FULL DDL: {raw_ddl}"
            )

            self.vectorstore.index_ddl(enhanced_ddl, table=table_name)
        log.info("DDLs Processed Successfully")

        # 3. Index Sample Values for Table Mapping
        try:
            log.info("Indexing sample values for value mapping...")
            all_tables = ddls["Table"].tolist()
            schema = getattr(self.database, 'current_schema', 'public')
            
            for table in all_tables:
                # Find columns that look like names, codes, or keys
                try:
                    column_query = f"""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name = '{table.lower()}' 
                        AND table_schema = '{schema}'
                        AND (column_name LIKE '%name%' OR column_name LIKE '%code%' OR column_name LIKE '%id%' OR column_name LIKE '%status%')
                        LIMIT 10;
                    """
                    col_df = self.database.execute_sql(connection, column_query)
                    if col_df is not None and not col_df.empty:
                        for col in col_df['column_name']:
                            sample_query = f'SELECT DISTINCT "{col}" FROM "{schema}"."{table}" WHERE "{col}" IS NOT NULL LIMIT 20;'
                            samples = self.database.execute_sql(connection, sample_query)
                            if samples is not None and not samples.empty:
                                vals = ", ".join([str(v) for v in samples[col].tolist() if v])
                                if vals:
                                    sample_doc = f"Table '{table}' contains values like: {vals} in column '{col}'"
                                    self.index(documentation=sample_doc)
                except Exception as e:
                    log.debug(f"Could not index samples for {table}: {e}")
        except Exception as e:
            log.warning(f"Value indexing failed: {e}")

        log.info("DDLs and Sample Values Indexed Successfully")

    def generate_auto_documentation(self, connection, db_name) -> str:
        """
        ⚠️ Bucket 2 Automation: Generates relationship documentation from metadata.
        """
        fk_df = self.database.get_foreign_keys(connection, db_name)
        if fk_df is None or fk_df.empty:
            return "No foreign key relationships detected in the database."
            
        doc = "AUTO-GENERATED DATABASE RELATIONSHIPS:\n\n"
        
        # Group by table
        tables = fk_df['table_name'].unique()
        for table in tables:
            doc += f"Table: {table}\n"
            table_fks = fk_df[fk_df['table_name'] == table]
            for _, row in table_fks.iterrows():
                doc += f"  - Relates to: {row['foreign_table_name']} via {row['column_name']} -> {row['foreign_column_name']}\n"
            doc += "\n"
            
        return doc
