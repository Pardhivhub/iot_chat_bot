# Deployment Guide

## Production Deployment

### Docker Deployment (Recommended)

#### 1. Create Dockerfile
```dockerfile
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Run application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### 2. Build & Run
```bash
# Build
docker build -t mindsql-backend .

# Run
docker run -d \
  -p 8000:8000 \
  -e CORE_DB_URL="postgresql://user:password@host:5432/db" \
  -e BACKEND_API_KEY="your-secure-key" \
  --name mindsql \
  mindsql-backend
```

#### 3. Docker Compose
```yaml
version: '3.8'

services:
  mindsql:
    build: .
    ports:
      - "8000:8000"
    environment:
      - CORE_DB_URL=${CORE_DB_URL}
      - BACKEND_API_KEY=${BACKEND_API_KEY}
      - OLLAMA_HOST=http://ollama:11434
    depends_on:
      - ollama
    restart: unless-stopped

  ollama:
    image: ollama/ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama-data:/root/.ollama
    restart: unless-stopped

volumes:
  ollama-data:
```

### Manual Deployment

#### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

#### 2. Configure Environment
```bash
# Production .env
CORE_DB_URL=postgresql://user:password@prod-db:5432/production_db
BACKEND_API_KEY=$(openssl rand -hex 32)
LOG_LEVEL=WARNING
LOG_FILE=/var/log/mindsql/app.log
```

#### 3. Run with Gunicorn
```bash
gunicorn app:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --access-logfile /var/log/mindsql/access.log \
  --error-logfile /var/log/mindsql/error.log
```

## Monitoring

### Health Check Endpoint
```bash
curl http://localhost:8000/health
```

**Expected Response:**
```json
{
  "status": "healthy",
  "database": {"connected": true, "url": "production_db"},
  "schema": {"indexed": true, "tables_count": 65}
}
```

### Monitoring Setup

#### Prometheus Metrics (Optional)
```bash
pip install prometheus-fastapi-instrumentator
```

Add to `app.py`:
```python
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(app)
```

#### Log Aggregation
- Use ELK Stack or CloudWatch
- Monitor for "FK Validation FAILED" errors
- Alert on `/health` failures

## Scaling

### Horizontal Scaling
```bash
# Run multiple instances behind load balancer
docker-compose up --scale mindsql=3
```

### Database Connection Pooling
Already configured in `config.py`:
```python
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=10
```

### Caching Strategy
- Golden Cache: Stores successful queries
- Vectorstore: FAISS indexes (persisted to disk)
- Consider Redis for distributed caching

## Security

### Production Checklist
- [ ] Changed `BACKEND_API_KEY` to secure random value
- [ ] Database connection uses SSL (`?sslmode=require`)
- [ ] Running behind HTTPS reverse proxy (nginx/Caddy)
- [ ] Firewall rules restrict database access
- [ ] Log files are rotated and secured
- [ ] Environment variables never committed

### SSL Configuration
```nginx
server {
    listen 443 ssl;
    server_name api.yourdomain.com;

    ssl_certificate /etc/ssl/certs/cert.pem;
    ssl_certificate_key /etc/ssl/private/key.pem;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Troubleshooting

### Container Won't Start
```bash
# Check logs
docker logs mindsql

# Common issue: Database connection
# Verify CORE_DB_URL is correct and accessible from container
```

### High Memory Usage
- Reduce `DATABASE_POOL_SIZE`
- Limit concurrent requests
- Monitor FAISS index size

### Slow Queries
- Check `/health` for schema indexing status
- Review logs for validation retries
- Consider caching frequently asked questions
