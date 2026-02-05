import os
import asyncio
import logging
from contextlib import asynccontextmanager

import pandas as pd
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from mindsql.core import MindSQLCore
from mindsql.databases import Postgres, SQLite
from mindsql.llms import DeepSeek
from mindsql.vectorstores import Faiss
from config import Config

# Load environment variables
load_dotenv()

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s"
)
logger = logging.getLogger("MindSQL-Backend")

# Configuration from centralized config
DB_URL = Config.DATABASE_URL
CHAT_MODEL = Config.OLLAMA_MODEL
OLLAMA_HOST = Config.OLLAMA_HOST
API_KEY = Config.API_KEY
API_KEY_NAME = "X-API-Key"

api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)

# Global MindSQL objects
llm = DeepSeek(
    model_config={'model': CHAT_MODEL},
    client_config={'host': OLLAMA_HOST}
)
# Select Database Engine
if DB_URL.startswith("sqlite"):
    database_engine = SQLite()
else:
    database_engine = Postgres()

minds = MindSQLCore(
    llm=llm,
    vectorstore=Faiss(),
    database=database_engine
)
db_connection = None

async def get_api_key(api_key: str = Security(api_key_header)):
    if api_key == API_KEY:
        return api_key
    raise HTTPException(
        status_code=403,
        detail="Could not validate credentials"
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    global db_connection
    logger.info("Initializing MindSQL Backend...")
    try:
        db_connection = minds.database.create_connection(url=DB_URL)
        logger.info("Connected to PostgreSQL successfully.")
        # Extract database name from URL automatically for database-agnostic operation
        from urllib.parse import urlparse
        parsed_url = urlparse(DB_URL)
        db_name = parsed_url.path.lstrip('/') or 'default_db'
        
        # 📂 PARTITION VECTORSTORE BY DB NAME (Zero Manual Work)
        storage_path = f"./vectorstore/{db_name}"
        minds.vectorstore.set_storage_path(storage_path)
        
        logger.info(f"Initializing MindSQL for database: {db_name}")
        logger.info("Starting DDL indexing...")
        minds.index_all_ddls(connection=db_connection, db_name=db_name)
        
        logger.info("Indexing schema relationships...")
        auto_doc = minds.generate_auto_documentation(connection=db_connection, db_name=db_name)
        minds.index(documentation=auto_doc)
        
        # Keep additional business logic documentation (Optional Bucket 2 Overrides)
        minds.index(documentation="""
        BUSINESS OVERRIDES:
        1. Plant names in database look like 'Plant 1 (West Dustin)'. 
        2. If a user asks for 'West Dustin', use: WHERE plant_name LIKE '%West Dustin%'
        3. Do NOT attempt to join machines to plants if no FK exists.
        """)
        
        logger.info("Schema relationship indexing complete.")
        logger.info("Backend fully initialized.")
        
    except Exception as e:
        logger.error(f"Initialization Failed: {e}")
        # In a real production app, you might want to retry or exit
    
    yield
    
    # Shutdown logic
    if db_connection:
        db_connection.close()
        logger.info("Database connection closed.")

app = FastAPI(
    title="MindSQL Production Backend",
    description="A production-ready API for natural language database interaction.",
    version="1.0.0",
    lifespan=lifespan
)

class QuestionRequest(BaseModel):
    question: str

@app.get("/", tags=["General"])
def read_root():
    return {
        "status": "online",
        "service": "MindSQL-DeepSeek",
        "configured_model": CHAT_MODEL
    }

@app.get("/health", tags=["General"])
def health_check():
    """
    Health check endpoint with detailed system status.
    Returns schema state, table count, and database connectivity.
    """
    status = {
        "status": "healthy" if db_connection else "unhealthy",
        "database": {
            "connected": db_connection is not None,
            "url": Config.get_db_name() if Config.DATABASE_URL else "unknown"
        },
        "schema": {
            "indexed": minds.relationship_graph is not None and len(minds.relationship_graph) > 0,
            "tables_count": len(minds.relationship_graph) if minds.relationship_graph else 0
        },
        "llm": {
            "model": CHAT_MODEL,
            "host": OLLAMA_HOST
        }
    }
    
    if not db_connection:
        raise HTTPException(status_code=503, detail="Database connection unavailable")
    
    return status

@app.post("/ask", tags=["Core"], dependencies=[Depends(get_api_key)])
async def ask_question(request: QuestionRequest):
    if not db_connection:
        raise HTTPException(status_code=503, detail="Database connection is not active")
    
    logger.info(f"Processing question: {request.question}")
    
    try:
        # Offload the heavy blocking work to a threadpool
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, minds.ask_db, db_connection, request.question)
        
        # Format result
        sql_result = []
        if result.get("sql_result") is not None:
            df = result.get("sql_result")
            if isinstance(df, pd.DataFrame):
                sql_result = df.to_dict(orient='records')

        return {
            "question": request.question,
            "sql": result.get("sql"),
            "result": sql_result,
            "answer": result.get("response")
        }
    except Exception as e:
        logger.error(f"Error executing query: {e}")
        raise HTTPException(status_code=500, detail="An internal server error occurred while processing your query.")

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
