# Quick Start Guide - LangGraph Resume Agent

## 📋 Prerequisites

- Python 3.11 or higher
- UV package manager (installed during setup)
- Local Ollama server running with `gpt-oss:latest` model (~10GB VRAM required)

## 🚀 Quick Setup

### Step 1: Initialize Dependencies
```bash
cd /Users/joyrana/personal/Demo
uv sync
```

This will:
- Create a virtual environment at `.venv`
- Install all required packages:
  - **LangGraph** - State machine orchestration
  - **Ollama** - Local LLM runtime
  - **FastAPI** - REST API server
  - **FastMCP** - Model Context Protocol support
  - **Uvicorn** - ASGI server

### Step 2: Start the API Server
```bash
./start.sh
```

Or manually:
```bash
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Expected output:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

### Step 3: Start the full monorepo
```bash
./dev.sh
```

This launches:
- Backend at `http://localhost:8000`
- Frontend at `http://localhost:5173`

### Step 4: Test the API

Open http://localhost:8000/docs in your browser for interactive API documentation.

Or use curl:
```bash
curl -X POST "http://localhost:8000/invoke" \
  -H "Content-Type: application/json" \
  -d '{"query": "What is 25 times 4?"}'
```

## 🎯 Usage Examples

### API Usage
```bash
# Invoke the agent
curl -X POST "http://localhost:8000/invoke" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Calculate 100 divided by 5",
    "max_steps": 5
  }'
```

### CLI Usage
```bash
./run-cli.sh
# or
uv run python cli.py
```

Interactive prompt:
```
You: What is 15 times 8?
Agent: The product of 15 times 8 is 120...
(Completed in 2 steps)

You: search for FastAPI tutorials
Agent: I found several tutorials about FastAPI...
```

### Python Script Usage
```python
import asyncio
from agent import get_react_agent

async def main():
    agent = await get_react_agent()
    result = await agent.invoke("What is 42 plus 58?")
    print(result)

asyncio.run(main())
```

Run with:
```bash
uv run python -c "$(cat example.py)"
```

## 📡 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/invoke` | Invoke the ReAct agent |
| GET | `/tools` | List available tools |
| GET | `/docs` | Interactive API documentation (Swagger UI) |

## 🔧 Configuration

Edit `.env` to customize:
```env
OLLAMA_BASE_URL=http://localhost:11434  # Local Ollama server
OLLAMA_MODEL=gpt-oss:latest              # Model name from `ollama list`
DEBUG=false                              # Enable/disable debug mode
```

## 📦 Available Tools

The agent can use these tools:

1. **calculator** - Math operations
   - Operations: add, subtract, multiply, divide
   
2. **search** - Information retrieval
   - Search queries
   
3. **web_fetch** - Content fetching
   - Fetch URLs

## 🧠 How the Agent Works

The ReAct pattern follows this cycle:

```
User Input
    ↓
Think (Grok generates reasoning)
    ↓
Act (Execute appropriate tool)
    ↓
Observe (Process tool output)
    ↓
Repeat until answer is ready
    ↓
Final Answer
```

## 🐛 Troubleshooting

### Dependencies not installing
```bash
# Clear cache and reinstall
rm -rf .venv
uv sync --refresh
```

### API key issues
Verify `GROK_API_KEY` in `.env`:
```bash
cat .env | grep GROK_API_KEY
```

### Port already in use
```bash
# Use a different port
uv run uvicorn main:app --port 8001
```

### Import errors
```bash
# Reinstall in development mode
uv sync
```

## 📚 Project Structure

```
Demo/
├── main.py              # FastAPI application
├── agent.py             # ReAct agent with LangGraph
├── llm.py              # Grok API integration
├── tools.py            # Tool definitions
├── config.py           # Configuration management
├── cli.py              # CLI interface
├── mcp_server.py       # FastMCP server
├── examples.py         # Usage examples
├── tests/              # Test suite
├── .env                # Environment variables
├── .env.example        # Example env file
├── pyproject.toml      # UV dependencies
├── start.sh            # Quick start script
├── run-cli.sh          # CLI runner script
└── README.md           # Full documentation
```

## 🎓 Next Steps

1. **Add more tools** - Edit `tools.py` to add custom tools
2. **Customize the agent** - Modify `agent.py` for different behaviors
3. **Deploy** - Use Docker or cloud platforms for production
4. **Extend with FastMCP** - Add MCP tools via `mcp_server.py`

## 📖 Documentation

- [LangGraph Docs](https://python.langchain.com/docs/langgraph)
- [FastAPI Docs](https://fastapi.tiangolo.com)
- [Grok API Docs](https://docs.x.ai)
- [UV Package Manager](https://docs.astral.sh/uv/)
