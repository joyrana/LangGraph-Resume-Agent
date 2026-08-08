# LangGraph Resume Agent

A full-stack AI-powered resume analysis and optimization platform. Built with **LangGraph**, **FastAPI**, **React/Vite**, and **local Ollama** inference. Features a ReAct agent that intelligently analyzes resumes and provides AI-driven optimization suggestions.

## Setup

### Prerequisites
- Python 3.11+
- UV package manager

### Installation

1. **Install UV** (if not already installed):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

2. **Install dependencies using UV**:
```bash
uv sync
```

3. **Create environment file**:
Copy the provided `.env` file (API key is already configured):
```bash
cp env.example .env
# Or use the pre-configured .env file
```

## Project Structure

```
.
├── config.py           # Configuration management
├── llm.py             # Ollama LLM integration
├── tools.py           # Tool definitions and execution
├── agent.py           # ReAct agent with LangGraph
├── mcp_server.py      # FastMCP server
├── main.py            # FastAPI application
├── cli.py             # CLI interface
├── .env               # Environment configuration
└── pyproject.toml     # Project dependencies (UV)
```

## Usage

### Run the full monorepo
```bash
./dev.sh
```

This starts:
- Backend: `http://localhost:8000`
- Frontend: `http://localhost:5173`

### 1. Start the FastAPI Server
```bash
uv run python main.py
```

The API will be available at `http://localhost:8000`

API Endpoints:
- `GET /health` - Health check
- `POST /invoke` - Invoke the agent
- `GET /tools` - List available tools

### 2. Use the CLI
```bash
uv run python cli.py
```

Example queries:
```
You: What is 25 times 4?
You: Search for Python machine learning libraries
You: What's the capital of France?
```

### 3. Use the FastAPI Interactive Docs
Open `http://localhost:8000/docs` in your browser for interactive API documentation.

## Agent Features

### ReAct Pattern
The agent follows the Reasoning and Acting (ReAct) pattern:
1. **Think** - Generate reasoning about the task
2. **Act** - Execute an appropriate tool
3. **Observe** - Process the tool output
4. **Repeat** - Until the final answer is reached

### Available Tools
- **calculator** - Perform arithmetic operations (add, subtract, multiply, divide)
- **search** - Search for information
- **web_fetch** - Fetch content from URLs

### LLM Integration
- Uses local Ollama for reasoning and planning
- Supports async operations
- Configurable temperature and token limits

## Development

### Running Tests
```bash
uv run python -m pytest tests/
```

### Running with Debug Mode
Edit `.env` and set `DEBUG=true`, then:
```bash
uv run python main.py
```

## Configuration

Edit `.env` to customize:
```
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gpt-oss:latest
DEBUG=false
```

## Architecture

### LangGraph State Machine
The agent is built as a state machine with nodes:
- **think** - Reasoning phase
- **act** - Action execution phase
- **observe** - Observation processing
- **answer** - Final answer preparation

### Async/Await
All operations are fully async-aware for high performance and scalability.

## Performance Notes

- The agent has a maximum of 10 steps per invocation (configurable)
- API responses are returned as soon as the final answer is ready
- All HTTP calls use connection pooling for efficiency

## Troubleshooting

### Import Errors
If you see import errors, ensure dependencies are installed:
```bash
uv sync --refresh
```

### API Key Issues
Verify the `GROK_API_KEY` in `.env` is correct and has proper permissions.

### Connection Errors
Ensure the Grok API is accessible from your network and API key is valid.
