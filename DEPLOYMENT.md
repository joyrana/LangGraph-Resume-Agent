# Deployment Guide

## Local Development

### Quick Start
```bash
cd /Users/joyrana/personal/Demo
uv sync
./start.sh
```

Access at: http://localhost:8000

## Docker Deployment

### Build Image
```bash
docker build -t langgraph-resume-agent .
```

### Run Container
```bash
docker run -p 8000:8000 \
  -e OLLAMA_BASE_URL=http://localhost:11434 \
  langgraph-resume-agent
```

### Docker Compose
```bash
docker-compose up -d
```

Check logs:
```bash
docker-compose logs -f grok-agent
```

## Kubernetes Deployment

### Create ConfigMap for Secrets
```bash
kubectl create secret generic grok-secrets \
  --from-literal=grok-api-key=your_key_here
```

### Deploy
```bash
kubectl apply -f k8s/deployment.yaml
```

### Example K8s Deployment (k8s/deployment.yaml)
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grok-react-agent
spec:
  replicas: 3
  selector:
    matchLabels:
      app: grok-agent
  template:
    metadata:
      labels:
        app: grok-agent
    spec:
      containers:
      - name: agent
        image: grok-react-agent:latest
        ports:
        - containerPort: 8000
        env:
        - name: GROK_API_KEY
          valueFrom:
            secretKeyRef:
              name: grok-secrets
              key: grok-api-key
        - name: DEBUG
          value: "false"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: grok-agent-service
spec:
  selector:
    app: grok-agent
  ports:
  - protocol: TCP
    port: 80
    targetPort: 8000
  type: LoadBalancer
```

## Cloud Platform Deployments

### Heroku
```bash
# Install Heroku CLI
# Create app
heroku create grok-react-agent

# Add buildpack for Python
heroku buildpacks:add heroku/python

# Set environment variables
heroku config:set GROK_API_KEY=your_key_here

# Deploy
git push heroku main

# View logs
heroku logs --tail
```

### AWS Lambda (with API Gateway)
```bash
# Use AWS SAM or Serverless Framework
# Package with: sam build
# Deploy with: sam deploy --guided
```

### Google Cloud Run
```bash
# Build and push to Container Registry
gcloud builds submit --tag gcr.io/PROJECT/grok-agent

# Deploy
gcloud run deploy grok-agent \
  --image gcr.io/PROJECT/grok-agent \
  --platform managed \
  --region us-central1 \
  --set-env-vars GROK_API_KEY=your_key_here \
  --allow-unauthenticated
```

### Azure Container Instances
```bash
# Build image
docker build -t grok-agent .

# Push to registry
docker tag grok-agent myregistry.azurecr.io/grok-agent:latest
docker push myregistry.azurecr.io/grok-agent:latest

# Deploy
az container create \
  --resource-group mygroup \
  --name grok-agent \
  --image myregistry.azurecr.io/grok-agent:latest \
  --environment-variables GROK_API_KEY=your_key_here
```

## Production Checklist

- [ ] Set `DEBUG=false` in `.env`
- [ ] Use strong, secure API keys
- [ ] Enable HTTPS
- [ ] Set up logging and monitoring
- [ ] Configure rate limiting
- [ ] Add authentication if needed
- [ ] Set up backup and recovery
- [ ] Configure CORS properly
- [ ] Load test the deployment
- [ ] Set up health checks
- [ ] Configure auto-scaling
- [ ] Enable request tracing
- [ ] Set up alerts and notifications

## Performance Optimization

### Gunicorn with Multiple Workers
```bash
uv run gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app
```

### Nginx Reverse Proxy
```nginx
server {
    listen 80;
    server_name api.example.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Redis Cache (Optional)
```python
# Add to main.py for response caching
from fastapi_cache2 import FastAPICache2
from fastapi_cache2.backends.redis import RedisBackend

# Configure cache at startup
FastAPICache2.init(RedisBackend(redis), prefix="grok-cache")
```

## Monitoring and Logging

### Prometheus Metrics
```python
# Add to main.py
from prometheus_client import Counter, Histogram

request_count = Counter('grok_requests_total', 'Total requests')
request_duration = Histogram('grok_request_duration_seconds', 'Request duration')
```

### ELK Stack Integration
```python
# Send logs to Elasticsearch
from pythonjsonlogger import jsonlogger

handler = logging.FileHandler('logs.json')
formatter = jsonlogger.JsonFormatter()
handler.setFormatter(formatter)
logger.addHandler(handler)
```

## Scaling Strategies

### Horizontal Scaling
- Deploy multiple instances behind load balancer
- Use container orchestration (Kubernetes)
- Cache responses in Redis

### Vertical Scaling
- Increase CPU/memory allocation
- Use faster instances
- Optimize Python code

### Database Optimization
- Add request caching
- Optimize LLM call batching
- Use connection pooling

## Security Considerations

1. **API Key Management**
   - Use environment variables or secrets manager
   - Never commit keys to version control
   - Rotate regularly

2. **Input Validation**
   - Validate all query parameters
   - Sanitize inputs
   - Prevent injection attacks

3. **HTTPS/TLS**
   - Always use HTTPS in production
   - Use valid certificates
   - Enable HSTS

4. **Rate Limiting**
   - Implement per-IP rate limits
   - Add authentication for higher limits
   - Monitor for abuse

5. **CORS**
   - Restrict to known origins
   - Use allowlists
   - Validate preflight requests

## Troubleshooting

### High Memory Usage
```bash
# Check process memory
ps aux | grep python

# Profile memory usage
python -m memory_profiler main.py
```

### Slow Requests
```bash
# Enable debug logging
DEBUG=true ./start.sh

# Use profiler
python -m cProfile -s cumtime main.py
```

### API Timeouts
- Increase `timeout` in config
- Optimize tool implementations
- Check Grok API status
- Consider request queuing

## Backup and Recovery

```bash
# Backup project
tar -czf grok-agent-backup.tar.gz .

# Export database
python export_data.py > data.json

# Restore from backup
tar -xzf grok-agent-backup.tar.gz
python import_data.py < data.json
```

## Updates and Maintenance

```bash
# Update dependencies
uv lock --upgrade

# Apply security patches
uv sync --refresh

# Run tests before deploying
uv run pytest tests/ -v

# Deploy new version
git tag v1.0.1
git push --tags
```
