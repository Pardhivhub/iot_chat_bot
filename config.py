"""
Centralized Configuration Management
Production-ready configuration for the SQL Agent
"""
import os
from typing import Optional
from urllib.parse import urlparse
import sys
from dotenv import load_dotenv

# Enhanced .env loading
env_paths = [
    os.path.join(os.getcwd(), '.env'),
    os.path.join(os.path.dirname(__file__), '.env'),
    os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), '.env') if sys.argv else None
]

found_env = False
for path in filter(None, env_paths):
    if os.path.exists(path):
        load_dotenv(path, override=True)
        print(f"✅ Configuration loaded from: {path}")
        found_env = True
        break

if not found_env:
    print("⚠️  WARNING: .env file not found! Using system environment variables or defaults.")

class Config:
    """Application configuration."""
    
    # Database
    DATABASE_URL: str = os.getenv("CORE_DB_URL") 
    
    DATABASE_SCHEMA: str = os.getenv("DB_SCHEMA", "itciot")
    DATABASE_POOL_SIZE: int = int(os.getenv("DATABASE_POOL_SIZE", "5"))
    DATABASE_MAX_OVERFLOW: int = int(os.getenv("DATABASE_MAX_OVERFLOW", "10"))
    
    @classmethod
    def get_db_name(cls) -> str:
        """Extract database name from URL."""
        return urlparse(cls.DATABASE_URL).path.lstrip('/') or 'default_db'
    
    # LLM Configuration
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2")
    
    # API Configuration
    API_KEY: str = os.getenv("BACKEND_API_KEY", "triniti-secret-key-2026")
    PORT: int = int(os.getenv("PORT", "8000"))
    HOST: str = os.getenv("HOST", "0.0.0.0")
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: Optional[str] = os.getenv("LOG_FILE", None)
    
    # Retry Configuration
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
    RETRY_DELAY_BASE: float = float(os.getenv("RETRY_DELAY_BASE", "2.0"))
    
    # Feature Flags
    AUTO_INDEX_ON_STARTUP: bool = os.getenv("AUTO_INDEX_ON_STARTUP", "true").lower() == "true"
    ENABLE_HEALTH_ENDPOINT: bool = os.getenv("ENABLE_HEALTH_ENDPOINT", "true").lower() == "true"
    
    @classmethod
    def validate(cls) -> bool:
        """Validate critical configuration."""
        if not cls.DATABASE_URL:
            print("\n❌ CRITICAL ERROR: DATABASE_URL is missing!")
            print("Please ensure CORE_DB_URL is set in your .env file.")
            print("Example: CORE_DB_URL=postgresql://user:pass@host:port/dbname\n")
            raise ValueError("CORE_DB_URL must be provided in .env or system environment.")
            
        if not cls.API_KEY or cls.API_KEY == "triniti-secret-key-2026":
            print("⚠️  WARNING: Using default API key. Set BACKEND_API_KEY in production!")
        return True

# Validate configuration on import
Config.validate()
