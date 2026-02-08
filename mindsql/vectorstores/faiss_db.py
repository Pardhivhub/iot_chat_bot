import json
import os
import uuid
import numpy as np
import pandas as pd
import faiss
import re

from . import IVectorstore
from .._utils import logger

log = logger.init_loggers("Faiss")

class SimpleEmbeddingFunction:
    def __init__(self, dimension=1024):
        self.dimension = dimension

    def encode(self, texts):
        # Very simple embedding: hash of the text to a fixed-size vector
        # This is just for testing and basic functionality without heavy dependencies
        vectors = []
        for text in texts:
            np.random.seed(hash(text) % (2**32))
            vectors.append(np.random.rand(self.dimension))
        return np.array(vectors)

class Faiss(IVectorstore):
    def __init__(self, config=None):
        if config is not None:
            directory = config.get("path", ".")
            self.dimension = config.get("dimension", 1024)
            self.embedding_function = config.get("embedding_function", SimpleEmbeddingFunction(self.dimension))
            self.index_builder = config.get("index_builder", faiss.IndexFlatL2)
        else:
            directory = "./vectorstore"
            if not os.path.exists(directory):
                os.makedirs(directory)

            self.dimension = 1024
            self.embedding_function = SimpleEmbeddingFunction(self.dimension)
            self.index_builder = faiss.IndexFlatL2

        self.directory = directory
        self.sql_index, self.sql_chunk_ids, self.sql_index_mapping = self._initialize_index()
        self.ddl_index, self.ddl_chunk_ids, self.ddl_index_mapping = self._initialize_index()
        self.documentation_index, self.documentation_chunk_ids, self.documentation_index_mapping = self._initialize_index()
        self.ddl_metadata = {}
        
        self._load_all()

    def _get_paths(self):
        return {
            "sql": os.path.join(self.directory, "sql.index"),
            "ddl": os.path.join(self.directory, "ddl.index"),
            "doc": os.path.join(self.directory, "doc.index"),
            "meta": os.path.join(self.directory, "metadata.json")
        }

    def _save_all(self):
        paths = self._get_paths()
        faiss.write_index(self.sql_index, paths["sql"])
        faiss.write_index(self.ddl_index, paths["ddl"])
        faiss.write_index(self.documentation_index, paths["doc"])
        
        meta = {
            "sql_chunks": self.sql_chunk_ids,
            "sql_mapping": self.sql_index_mapping,
            "ddl_chunks": self.ddl_chunk_ids,
            "ddl_mapping": self.ddl_index_mapping,
            "doc_chunks": self.documentation_chunk_ids,
            "doc_mapping": self.documentation_index_mapping,
            "ddl_metadata": self.ddl_metadata
        }
        
        # Atomic write using temporary file
        temp_path = paths["meta"] + ".tmp"
        try:
            with open(temp_path, 'w') as f:
                json.dump(meta, f)
            os.replace(temp_path, paths["meta"])
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e

    def _load_all(self):
        paths = self._get_paths()
        if os.path.exists(paths["meta"]):
            try:
                self.sql_index = faiss.read_index(paths["sql"])
                self.ddl_index = faiss.read_index(paths["ddl"])
                self.documentation_index = faiss.read_index(paths["doc"])
                
                with open(paths["meta"], 'r') as f:
                    meta = json.load(f)
                    self.sql_chunk_ids = meta["sql_chunks"]
                    self.sql_index_mapping = meta["sql_mapping"]
                    self.ddl_chunk_ids = meta["ddl_chunks"]
                    self.ddl_index_mapping = meta["ddl_mapping"]
                    self.documentation_chunk_ids = meta["doc_chunks"]
                    self.documentation_index_mapping = meta["doc_mapping"]
                    self.ddl_metadata = meta.get("ddl_metadata", {})
            except Exception as e:
                log.error(f"Error loading vector store: {e}")
                # Self-healing: if corrupted, move aside or clear
                try:
                    os.rename(paths["meta"], paths["meta"] + ".corrupt")
                except:
                    pass

    def _initialize_index(self) -> tuple:
        index = self.index_builder(self.dimension)
        chunk_ids = []
        index_mapping = {}
        return index, chunk_ids, index_mapping

    def index_question_sql(self, question: str, sql: str, **kwargs) -> str:
        question_sql_json = json.dumps({"Question": question, "SQLQuery": sql}, ensure_ascii=False)
        chunk_id = str(uuid.uuid4()) + "-sql"
        vectors = self.embedding_function.encode([question_sql_json]).astype('float32')
        self.sql_index.add(vectors)
        self.sql_chunk_ids.append(question_sql_json)
        self.sql_index_mapping[chunk_id] = len(self.sql_chunk_ids) - 1
        self._save_all()
        return chunk_id

    def index_ddl(self, ddl: str, **kwargs) -> str:
        chunk_id = str(uuid.uuid4()) + "-ddl"
        table_name = kwargs.get('table', None)
        vectors = self.embedding_function.encode([ddl]).astype('float32')
        self.ddl_index.add(vectors)
        self.ddl_chunk_ids.append(ddl)
        self.ddl_index_mapping[chunk_id] = len(self.ddl_chunk_ids) - 1
        self.ddl_metadata[chunk_id] = {'table': table_name}
        self._save_all()
        return chunk_id

    def index_documentation(self, documentation: str, **kwargs) -> str:
        chunk_id = str(uuid.uuid4()) + "-doc"
        vectors = self.embedding_function.encode([documentation]).astype('float32')
        self.documentation_index.add(vectors)
        self.documentation_chunk_ids.append(documentation)
        self.documentation_index_mapping[chunk_id] = len(self.documentation_chunk_ids) - 1
        self._save_all()
        return chunk_id

    def fetch_all_vectorstore_data(self, **kwargs) -> pd.DataFrame:
        combined_data = []
        for index, chunk_ids, index_mapping, index_name in zip(
                [self.sql_index, self.ddl_index, self.documentation_index],
                [self.sql_chunk_ids, self.ddl_chunk_ids, self.documentation_chunk_ids],
                [self.sql_index_mapping, self.ddl_index_mapping, self.documentation_index_mapping],
                ["sql", "ddl", "documentation"]):
            n_total = index.ntotal
            if n_total > 0:
                for idx in range(n_total):
                    combined_data.append([chunk_ids[idx], None, None, index_name])
        cols = ['id', 'question', 'content', 'training_data_type']
        return pd.DataFrame(combined_data, columns=cols)

    def delete_vectorstore_data(self, item_id: str, **kwargs) -> bool:
        # Simplification: not implementing complex removal for mock
        return False

    def remove_collection(self, collection_name: str) -> bool:
        if collection_name == "sql":
            self.sql_index.reset()
            self.sql_chunk_ids.clear()
            self.sql_index_mapping.clear()
        elif collection_name == "ddl":
            self.ddl_index.reset()
            self.ddl_chunk_ids.clear()
            self.ddl_index_mapping.clear()
        elif collection_name == "documentation":
            self.documentation_index.reset()
            self.documentation_chunk_ids.clear()
            self.documentation_index_mapping.clear()
        else:
            return False
        return True

    def retrieve_relevant_question_sql(self, question: str, **kwargs) -> list:
        vectors = self.embedding_function.encode([question]).astype('float32')
        k = min(kwargs.get('k', 2), self.sql_index.ntotal)
        if k == 0:
            return []
        distances, indices = self.sql_index.search(vectors, k)
        result = []
        for idx in indices[0]:
            if idx != -1 and idx < len(self.sql_chunk_ids):
                result.append(json.loads(self.sql_chunk_ids[idx]))
        return result

    def set_storage_path(self, path: str, **kwargs) -> None:
        """
        Updates the storage directory and reloads indices.
        """
        if not os.path.exists(path):
            os.makedirs(path)
        
        self.directory = path
        # Re-initialize indices to empty state
        self.sql_index, self.sql_chunk_ids, self.sql_index_mapping = self._initialize_index()
        self.ddl_index, self.ddl_chunk_ids, self.ddl_index_mapping = self._initialize_index()
        self.documentation_index, self.documentation_chunk_ids, self.documentation_index_mapping = self._initialize_index()
        self.ddl_metadata = {}
        
        # Load if index files already exist in the new path
        self._load_all()

    def retrieve_relevant_ddl(self, question: str, **kwargs) -> list:
        query_lower = question.lower()
        
        # 1. First, check for exact or fuzzy table name matches in the question
        exact_matches = []
        for ddl in self.ddl_chunk_ids:
            # Simple table name extraction from CREATE TABLE "name" or CREATE TABLE name
            match = re.search(r'CREATE TABLE "?([\w]+)"?', ddl, re.IGNORECASE)
            if match:
                table_name = match.group(1).lower()
                # Match if table name is in query, OR query word is in table name (singular/plural fix)
                if table_name in query_lower:
                    exact_matches.append(ddl)
                else:
                    # Check if any part of the table name is an EXACT match to a query word
                    table_parts = table_name.split('_')
                    query_words = re.findall(r'\w+', query_lower)
                    for word in query_words:
                        if len(word) >= 4 and word in table_parts:
                            exact_matches.append(ddl)
                            break
        
        # 2. Get vector results
        vectors = self.embedding_function.encode([question]).astype('float32')
        k = min(kwargs.get('k', 2), self.ddl_index.ntotal)
        
        if k == 0:
            return exact_matches
            
        distances, indices = self.ddl_index.search(vectors, k)
        vector_results = []
        for idx in indices[0]:
            if idx != -1 and idx < len(self.ddl_chunk_ids):
                ddl = self.ddl_chunk_ids[idx]
                if ddl not in exact_matches:
                    vector_results.append(ddl)
        
        # Combine matches, prioritizing exact table name hits
        # Limit total results to k (or more if many exact matches)
        combined = exact_matches + vector_results
        return combined[:max(k, len(exact_matches))]

    def retrieve_relevant_documentation(self, question: str, **kwargs) -> list:
        vectors = self.embedding_function.encode([question]).astype('float32')
        k = min(kwargs.get('k', 2), self.documentation_index.ntotal)
        if k == 0:
            return []
        distances, indices = self.documentation_index.search(vectors, k)
        result = []
        for idx in indices[0]:
            if idx != -1 and idx < len(self.documentation_chunk_ids):
                result.append(self.documentation_chunk_ids[idx])
        return result
