# LangGraph Resume Agent - API Usage Examples

## 1. Health Check
```bash
curl http://localhost:8000/health
```

Response:
```json
{
  "status": "healthy",
  "message": "ReAct Agent API is running"
}
```

## 2. List Available Tools
```bash
curl http://localhost:8000/tools
```

Response:
```json
{
  "tools": ["calculator", "search", "web_fetch"],
  "details": {
    "calculator": {
      "description": "Useful for math calculations",
      "params": {
        "operation": "str - mathematical operation",
        "a": "float - first number",
        "b": "float - second number"
      }
    },
    ...
  }
}
```

## 3. Invoke the Agent

### Simple Query
```bash
curl -X POST http://localhost:8000/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is 25 times 4?"
  }'
```

Response:
```json
{
  "input": "What is 25 times 4?",
  "output": "The result of 25 times 4 is 100.",
  "steps": 2,
  "status": "success"
}
```

### With Max Steps Control
```bash
curl -X POST http://localhost:8000/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Search for machine learning frameworks",
    "max_steps": 5
  }'
```

## 4. Using Python Requests

```python
import requests

response = requests.post(
    "http://localhost:8000/invoke",
    json={"query": "Calculate 100 divided by 4"}
)

result = response.json()
print(f"Output: {result['output']}")
print(f"Steps: {result['steps']}")
```

## 5. Using HTTPX (Async)

```python
import httpx
import asyncio

async def invoke_agent():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/invoke",
            json={"query": "What is 10 plus 20?"}
        )
        return response.json()

result = asyncio.run(invoke_agent())
print(result)
```

## 6. Batch Requests

```python
import httpx
import asyncio

async def batch_invoke():
    queries = [
        "What is 15 times 3?",
        "Search for Python async tutorials",
        "Calculate 1000 divided by 25"
    ]
    
    async with httpx.AsyncClient() as client:
        tasks = [
            client.post(
                "http://localhost:8000/invoke",
                json={"query": q}
            )
            for q in queries
        ]
        responses = await asyncio.gather(*tasks)
        return [r.json() for r in responses]

results = asyncio.run(batch_invoke())
for r in results:
    print(f"Q: {r['input']}")
    print(f"A: {r['output']}\n")
```

## 7. Swagger UI

Visit: `http://localhost:8000/docs`

Features:
- Interactive request builder
- Live testing
- Schema documentation
- Example responses

## 8. ReDoc Documentation

Visit: `http://localhost:8000/redoc`

Alternative documentation format

## 9. OpenAPI Schema

```bash
curl http://localhost:8000/openapi.json | python -m json.tool
```

## 10. Error Handling

### Invalid Query
```bash
curl -X POST http://localhost:8000/invoke \
  -H "Content-Type: application/json" \
  -d '{"query": ""}'
```

### Max Steps Exceeded
The agent will stop after reaching max_steps and return the best answer found so far.

### API Key Issues
Ensure Ollama is running at `http://localhost:11434` with `gpt-oss:latest` available.

## Response Structure

```json
{
  "input": "string - the user's query",
  "output": "string - the agent's final answer",
  "steps": "integer - number of steps taken",
  "status": "string - 'success' or error message"
}
```

## Headers

All requests should include:
```
Content-Type: application/json
```

## Timeouts

- Default: 30 seconds per request
- Configurable per request via `max_steps`

## Rate Limiting

Currently unlimited, but can be added via FastAPI middleware.

## Authentication

Currently no authentication. To add:
1. Add `Security` to FastAPI
2. Implement token validation
3. Update headers requirement

## CORS

Currently allows all origins. Restrict in `main.py`:
```python
allow_origins=["https://example.com"]
```
