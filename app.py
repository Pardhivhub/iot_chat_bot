import os
import asyncio
import logging
from contextlib import asynccontextmanager

import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from mindsql.core import MindSQLCore
from mindsql.databases import Postgres, SQLite
from mindsql.llms import DeepSeek
from mindsql.vectorstores import Faiss
from config import Config

# Configuration from centralized config
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
        1. Plant names in database look like 'Plant 1 (West Dustin)' and real names like 'Tesla Giga Berlin'.
        2. If a user asks for specific plant names like 'Tesla Giga Berlin', use: WHERE plant_name ILIKE '%Tesla Giga Berlin%'
        
        IOT SEMANTIC MAPPING:
        1. 'temperature', 'humidity', 'pressure', 'reading', 'value' -> sensor_readings table.
        2. 'alert', 'critical', 'fault' -> sensor_alerts table.
        3. 'machine status', 'running', 'offline', 'standby' -> machines table.
        4. 'maintenance', 'repair', 'work order', 'maintenance history' -> maintenance_requests table, maintenance_work_orders table.
        5. 'last hour', 'last 24 hours', 'recent', 'today' -> use WHERE timestamp > NOW() - INTERVAL.
        6. 'per region', 'per plant', 'by group', 'each' -> use GROUP BY.
        
        CRITICAL RELATIONSHIP HINTS:
        1. machines → line_machines → production_lines (machine_id connects both)
        2. machines → machine_sensors → sensors (machine_id and sensor_id connect)
        3. employees → job_roles (role_id connects directly)
        4. plants → regions (region_id connects directly)
        5. plants → production_lines (plant_id connects directly)
        6. production_lines → line_machines → machines (line_id and machine_id connect)
        
        COMMON JOIN PATTERNS:
        - Machines with Production Lines: 
          SELECT m.machine_name, m.status, pl.line_name 
          FROM machines m 
          JOIN line_machines lm ON m.machine_id = lm.machine_id 
          JOIN production_lines pl ON lm.line_id = pl.line_id
          
        - Sensors with Machines:
          SELECT s.sensor_id, s.model_number, m.machine_name 
          FROM sensors s 
          JOIN machine_sensors ms ON s.sensor_id = ms.sensor_id 
          JOIN machines m ON ms.machine_id = m.machine_id
          
        - Employees with Job Roles:
          SELECT e.first_name, e.last_name, jr.role_name 
          FROM employees e 
          JOIN job_roles jr ON e.role_id = jr.role_id
          
        - Plants with Regions:
          SELECT p.plant_name, p.status, r.region_name 
          FROM plants p 
          JOIN regions r ON p.region_id = r.region_id
        
        WORKING MACHINE NAMES: Machines are named like 'Machine-001', 'Machine-043', etc.
        SENSOR MODEL NUMBERS: Format varies - 'PX-9', 'SENSE-8787', etc.
        USE ILIKE for case-insensitive string matching in PostgreSQL.
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
        # Note: run_in_executor does not support keyword arguments.
        # Signature: minds.ask_db(connection, question, table_names, visualize, **kwargs)
        result = await loop.run_in_executor(None, minds.ask_db, db_connection, request.question, None, False)
        
        # Format result
        sql_result = []
        if result.get("sql_result") is not None:
            df = result.get("sql_result")
            if isinstance(df, pd.DataFrame):
                sql_result = df.to_dict(orient='records')

        # Serialize result to be JSON safe (handle datetimes, decimals, etc.)
        import json
        from datetime import datetime
        class DateTimeEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, datetime):
                    return obj.isoformat()
                return str(obj)

        json_safe_result = json.loads(json.dumps(sql_result, cls=DateTimeEncoder))

        return {
            "question": request.question,
            "sql": result.get("sql"),
            "result": json_safe_result,
            "answer": result.get("response")
        }
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Internal Server Error: {str(e)}\n{tb}")
        raise HTTPException(status_code=500, detail=f"Internal Error: {str(e)}") # Return actual error for debugging

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
