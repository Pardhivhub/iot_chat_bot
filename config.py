"""
Centralized Configuration Management
Production-ready configuration for the SQL Agent
"""
import os
from typing import Optional
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Application configuration."""
    
    # Database
    DATABASE_URL: str = os.getenv("CORE_DB_URL", "postgresql://postgres@localhost:5432/stress_test_db")
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
            raise ValueError("DATABASE_URL is required")
        if not cls.API_KEY or cls.API_KEY == "triniti-secret-key-2026":
            print("⚠️  WARNING: Using default API key. Set BACKEND_API_KEY in production!")
        return True

# Validate configuration on import
Config.validate()
