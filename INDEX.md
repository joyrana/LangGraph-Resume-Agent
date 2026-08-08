# 📚 LangGraph Resume Agent - Complete Project Index

Full-stack AI resume analyzer using LangGraph, FastAPI, React, and Ollama. This document helps you navigate the entire project structure and documentation.

## 🎯 Quick Navigation

### First Time? Start Here
1. Read [QUICKSTART.md](QUICKSTART.md) - 5 minute setup guide
2. Run `./dev.sh` - Start backend and frontend together
3. Open [http://localhost:5173](http://localhost:5173) - Frontend app
4. Open [http://localhost:8000/docs](http://localhost:8000/docs) - Interactive API

### Want to Understand the Project?
1. Read [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) - High-level overview
2. Review [README.md](README.md) - Detailed documentation
3. Check [ARCHITECTURE.md](#) - Technical deep dive

### Need API Examples?
- See [API_EXAMPLES.md](API_EXAMPLES.md) - Real usage examples with curl, Python, etc.

### Ready to Deploy?
- See [DEPLOYMENT.md](DEPLOYMENT.md) - Production deployment guide

---

## 📁 Project Structure

### Core Agent Files
| File | Purpose | Size |
|------|---------|------|
| [agent.py](agent.py) | ReAct agent with LangGraph state machine | 5.6 KB |
| [llm.py](llm.py) | Ollama LLM client with async support | 1.7 KB |
| [tools.py](tools.py) | Tool definitions and execution framework | 3.1 KB |
| [config.py](config.py) | Environment configuration management | 516 B |
| [mcp_server.py](mcp_server.py) | FastMCP server for MCP protocol | 672 B |

### Server & API
| File | Purpose | Size |
|------|---------|------|
| [main.py](main.py) | FastAPI application and REST endpoints | 3.3 KB |
| [cli.py](cli.py) | Interactive command-line interface | 1.0 KB |
| [examples.py](examples.py) | Usage example scripts | 631 B |

### Configuration Files
| File | Purpose |
|------|---------|
| [pyproject.toml](pyproject.toml) | UV dependencies and project metadata |
| [uv.lock](uv.lock) | Locked dependency versions (auto-generated) |
| [.env](.env) | Environment variables (API keys, config) |
| [.env.example](env.example) | Example environment variables |
| [.gitignore](.gitignore) | Git ignore patterns |

### Docker & Deployment
| File | Purpose |
|------|---------|
| [Dockerfile](Dockerfile) | Docker container image |
| [docker-compose.yml](docker-compose.yml) | Multi-container orchestration |

### Startup Scripts
| File | Purpose |
|------|---------|
| [start.sh](start.sh) | 🚀 Start FastAPI server |
| [run-cli.sh](run-cli.sh) | 💻 Start CLI interface |

### Documentation
| File | Size | Purpose |
|------|------|---------|
| [QUICKSTART.md](QUICKSTART.md) | 4.5 KB | Quick setup & usage guide |
| [README.md](README.md) | 3.3 KB | Full project documentation |
| [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) | 6.8 KB | High-level overview & architecture |
| [API_EXAMPLES.md](API_EXAMPLES.md) | 3.8 KB | API usage examples with curl/Python |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 6.3 KB | Production deployment guide |
| **INDEX.md** | This file | Navigation guide |

### Testing
| File | Purpose |
|------|---------|
| [tests/test_agent.py](tests/test_agent.py) | Agent unit tests |
| [tests/test_tools.py](tests/test_tools.py) | Tool integration tests |

---

## 🚀 Quick Commands

### Setup
```bash
cd /Users/joyrana/personal/Demo
uv sync                    # Install dependencies (one-time)
cd frontend && npm install # Install frontend dependencies (one-time)
```

### Run
```bash
./dev.sh                   # Start backend + frontend together
./start.sh                 # Start API server on port 8000
./run-cli.sh               # Start interactive CLI
uv run python examples.py  # Run example script
```

### Test
```bash
uv run pytest tests/ -v    # Run all tests
uv run pytest tests/test_agent.py -v  # Run specific test
```

### Cleanup
```bash
rm -rf .venv              # Remove virtual environment
rm uv.lock                # Remove lock file
uv sync --refresh         # Reinstall fresh
```

---

## 📖 Documentation Guide

### For Different Use Cases

**I want to get started quickly**
→ [QUICKSTART.md](QUICKSTART.md)

**I need API usage examples**
→ [API_EXAMPLES.md](API_EXAMPLES.md)

**I want to understand the architecture**
→ [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)

**I need detailed documentation**
→ [README.md](README.md)

**I want to deploy to production**
→ [DEPLOYMENT.md](DEPLOYMENT.md)

**I need to navigate the project**
→ This file (INDEX.md)

---

## 🔑 Key Concepts

### ReAct Pattern
The agent follows Reasoning + Acting:
```
User Input → Think → Act → Observe → Repeat → Final Answer
```

### LangGraph State Machine
Orchestrates the agent flow with four nodes:
- **think** - Generate reasoning from local Ollama LLM
- **act** - Execute the selected tool
- **observe** - Process tool output
- **answer** - Prepare final response

### Available Tools
1. **calculator** - Math operations (add, subtract, multiply, divide)
2. **search** - Information retrieval
3. **web_fetch** - URL content fetching

### Three Interfaces
1. **REST API** - `POST /invoke` - For web/mobile apps
2. **CLI** - Interactive prompt - For manual testing
3. **Python SDK** - Direct async/await - For automation

---

## 📊 File Dependencies

```
main.py (FastAPI)
├── agent.py (ReAct Agent)
│   ├── llm.py (Ollama integration)
│   ├── tools.py (Tool Execution)
│   └── config.py (Settings)
├── config.py (Settings)
└── tools.py (Tool Definitions)

cli.py (CLI)
└── agent.py

mcp_server.py (FastMCP)
└── agent.py
```

---

## 🎓 Learning Path

### Beginner
1. [QUICKSTART.md](QUICKSTART.md) - Learn to run it
2. [API_EXAMPLES.md](API_EXAMPLES.md) - See how to use it
3. Open http://localhost:8000/docs - Explore API interactively

### Intermediate
1. [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) - Understand architecture
2. Review [agent.py](agent.py) - Learn ReAct implementation
3. Review [llm.py](llm.py) - Learn LLM integration
4. Modify [tools.py](tools.py) - Add a custom tool

### Advanced
1. [README.md](README.md) - Deep technical details
2. [DEPLOYMENT.md](DEPLOYMENT.md) - Production considerations
3. Extend the agent - Add multi-agent coordination
4. Integrate with databases or APIs

---

## 🐛 Troubleshooting

| Issue | Solution |
|-------|----------|
| Dependencies won't install | See [QUICKSTART.md](QUICKSTART.md#troubleshooting) |
| API won't start | Ensure Ollama is running at http://localhost:11434 |
| Port 8000 in use | Run on different port: `uv run uvicorn main:app --port 8001` |
| Import errors | Run `uv sync --refresh` |
| Tests failing | Check Python version is 3.11+ and all dependencies installed |

---

## 📦 What's Installed

**Core Dependencies**
- langgraph - Agent orchestration
- langchain - LLM framework
- fastapi - Web server
- uvicorn - ASGI server
- httpx - HTTP client
- pydantic - Data validation
- fastmcp - MCP protocol

**Testing**
- pytest - Test framework
- pytest-asyncio - Async test support

Total: 102 packages (all installed via `uv sync`)

---

## 🔒 Security Notes

- API key stored in `.env` (never commit to git)
- `.gitignore` configured to exclude sensitive files
- No authentication by default (add if needed)
- CORS open to all origins (restrict if needed)

---

## 📞 Support Resources

- **LangGraph**: https://python.langchain.com/docs/langgraph
- **FastAPI**: https://fastapi.tiangolo.com
- **Ollama**: https://ollama.ai
- **UV Package Manager**: https://docs.astral.sh/uv/

---

## 🎯 Common Tasks

### Add a New Tool
1. Implement async function in [tools.py](tools.py)
2. Register in `TOOLS` dictionary
3. Agent automatically discovers it

### Change LLM Model
1. Edit `.env`
2. Change `OLLAMA_MODEL=value`

### Deploy to Production
1. Follow [DEPLOYMENT.md](DEPLOYMENT.md)
2. Use Docker or cloud platform

### Add Authentication
1. Add FastAPI Security
2. Update [main.py](main.py)
3. Protect endpoints

### Enable HTTPS
1. Use reverse proxy (nginx)
2. Add SSL certificate
3. Configure in deployment

### Monitor Performance
1. Enable debug logging
2. Use profiling tools
3. Check API response times

---

## ✅ Project Status

- ✅ Core agent implemented
- ✅ FastAPI server working
- ✅ CLI interface ready
- ✅ Tests included
- ✅ Docker support
- ✅ Complete documentation
- ✅ Ready for production use

---

## 🎉 Next Steps

1. **Start the server**: `./start.sh`
2. **Test the API**: Visit http://localhost:8000/docs
3. **Try the CLI**: `./run-cli.sh`
4. **Read documentation**: Pick one doc above based on your needs
5. **Deploy**: Follow [DEPLOYMENT.md](DEPLOYMENT.md)

---

**Last Updated**: August 8, 2026  
**Project Version**: 0.1.0  
**Python Version**: 3.11+  
**Status**: ✅ Production Ready
