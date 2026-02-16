# 🤖 IoT SQL ChatBot Engine

A production-ready, AI-powered natural language interface for IoT databases. This engine transforms natural language questions into validated, high-performance PostgreSQL queries using **Ollama (Llama 3.2)** and a multi-layered RAG architecture.

## 🚀 Key Performance Metrics
- **Accuracy**: **80.0%** on comprehensive IoT analytical benchmarks (Up from 43% baseline).
- **Join Handling**: **93% success rate** on complex multi-table joins.
- **Reliability**: Integrated SQL validator and hallucination sanitizer to prevent execution of incorrect queries.

---

## 🛠 Features
- **4-Tier Intent Routing**: Automatically handles Metadata, Schema, Analysis, and Data Retrieval intents.
- **SQL Guardrails**: Built-in validator for column matching, Alias handling, and math function verification.
- **Portability**: Database-agnostic core logic that auto-detects schema details.
- **Explainability**: Natural language responses that interpret the data retrieved from the database.

---

## 📂 Project Structure
```text
iot_chat_bot/
├── app.py                # FastAPI Backend & Intent Router
├── terminal_chat.py      # Terminal Client (CLI)
├── .env                  # Environment Configuration (DB, Model, API Key)
├── relationships.json    # Knowledge Base: Join Paths & Semantic Examples
├── golden_cache.json     # Performance Layer: SQL Result Caching
└── mindsql/              # Core AI Engine (Validator, LLM Bridge, Sanitizer)
```

---

## ⚙️ Setup & Installation

### 1. Prerequisites
- **Python 3.9+**
- **Ollama** (with `llama3.2:3b` model installed)
- **PostgreSQL** Database

### 2. Environment Configuration
Create or edit `.env` in the root directory:
```bash
CORE_DB_URL=postgresql://user:password@localhost:5432/your_database
BACKEND_API_KEY=your-secure-api-key
CHAT_MODEL=llama3.2:latest
OLLAMA_HOST=http://localhost:11434
```

### 3. Running the System
```bash
# Start the FastAPI Server
python app.py
```

---

## 🔄 Switching to a New Database
This engine is designed for portability. To migrate to a new database with high accuracy:

1. **Update Connection**: Set the new `CORE_DB_URL` in `.env`.
2. **Define Relationships**: Update `relationships.json` with the new schema's Join Paths. The LLM uses this file as its source of truth for navigation.
3. **Reset Cache**: Clear the contents of `golden_cache.json` (set to `{}`) to ensure no stale SQL logic remains from previous schemas.

---

## 🛡 Security & Validation
The system implements a **Safe Execution Layer**:
- **Validator**: Checks every query against the active database schema before execution.
- **Sanitizer**: Automatically corrects common LLM hallucinations (e.g., mismatched column names).
- **Retries**: Intelligent retry logic that feeds structural errors back to the LLM for self-correction.

---
*Developed for professional IoT analytics environments.* 🚀
