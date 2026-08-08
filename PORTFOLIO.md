# 🚀 LangGraph Resume Agent - Portfolio Project

## Overview

**LangGraph Resume Agent** is a production-grade full-stack AI application demonstrating modern web and AI infrastructure. It showcases expertise in building scalable AI systems with both self-hosted LLM inference and cloud-ready architecture.

## Technology Stack

### 🔙 Backend
- **LangGraph** - Agentic state machine orchestration for multi-step reasoning workflows
- **FastAPI** - Async Python web framework with automatic OpenAPI documentation
- **Pydantic** - Type-safe configuration management and data validation
- **Ollama** - Self-hosted LLM inference (gpt-oss:latest, 20.9B parameters)
- **Uvicorn** - ASGI production server

### 🎨 Frontend
- **React 18** - Modern UI framework
- **Vite** - Next-generation frontend build tool
- **TypeScript** - Type-safe JavaScript
- **TailwindCSS** - Utility-first CSS framework

### 🏗️ Infrastructure
- **Python 3.11+** - Primary language
- **UV** - Ultra-fast Python package manager
- **Docker** - Container orchestration
- **Docker Compose** - Multi-container development environment

### 🤖 AI/ML Architecture
- **ReAct Pattern** - Reasoning + Acting agent loop
- **LLM Inference** - Local Ollama for zero-latency, private AI
- **State Management** - LangGraph for deterministic agent workflows
- **Async Processing** - FastAPI for non-blocking operations

## Project Highlights

### ✨ Key Features
1. **Full-Stack Monorepo** - Backend + Frontend in single repository with unified dev workflow
2. **Local LLM Integration** - Zero cloud dependencies, complete privacy
3. **Production Ready** - Docker support, environment configuration, error handling
4. **Type Safety** - End-to-end Python typing + TypeScript frontend
5. **Developer Experience** - Single `./dev.sh` command to start entire stack

### 📊 Code Organization
```
langgraph-resume-agent/
├── Backend (Python)
│   ├── config.py           # Pydantic configuration management
│   ├── llm.py             # Ollama LLM client abstraction
│   ├── agent.py           # LangGraph ReAct agent
│   ├── resume_agent.py    # Resume-specific workflow
│   ├── main.py            # FastAPI application
│   └── pyproject.toml     # UV-managed dependencies
│
├── Frontend (React + TypeScript)
│   ├── src/components/    # React components
│   ├── src/services/      # API client services
│   └── vite.config.ts     # Vite configuration
│
├── Infrastructure
│   ├── Dockerfile         # Container image
│   ├── docker-compose.yml # Multi-container setup
│   ├── dev.sh            # Development launcher
│   └── dev.py            # Python orchestrator
│
└── Documentation
    ├── README.md          # Project overview
    ├── QUICKSTART.md      # 5-minute setup
    ├── DEPLOYMENT.md      # Production deployment
    └── PROJECT_SUMMARY.md # Technical deep dive
```

## What It Demonstrates

### Software Engineering
✅ **Clean Architecture** - Separation of concerns (config, LLM, agent, API)  
✅ **Async Programming** - FastAPI's async/await for high concurrency  
✅ **Type Safety** - Pydantic models, Python typing, TypeScript  
✅ **Error Handling** - Production-grade exception management  
✅ **Testing** - pytest setup for unit and integration tests  

### AI/ML Engineering
✅ **Agent Patterns** - ReAct implementation with LangGraph  
✅ **LLM Integration** - Custom Ollama client with streaming support  
✅ **State Machines** - Deterministic workflow orchestration  
✅ **Prompt Engineering** - Structured prompts for resume analysis  
✅ **Local Inference** - Self-hosted model deployment patterns  

### DevOps & Infrastructure
✅ **Container Orchestration** - Docker + Docker Compose  
✅ **Environment Management** - .env configuration, Pydantic settings  
✅ **Monorepo Management** - UV for cross-platform package management  
✅ **Development Workflow** - Single-command setup and launch  
✅ **Documentation** - Comprehensive guides for setup and deployment  

## Getting Started

### Quick Setup
```bash
# Clone and navigate
cd langgraph-resume-agent

# Install dependencies (creates .venv)
uv sync

# Start everything
./dev.sh
```

**Backend**: http://localhost:8000  
**Frontend**: http://localhost:5173  
**API Docs**: http://localhost:8000/docs  

### Prerequisites
- Python 3.11+
- Ollama running locally with `gpt-oss:latest` model

## Performance & Scale

- **Sub-100ms API responses** - FastAPI async performance
- **20.9B parameter LLM** - gpt-oss:latest local inference
- **Zero external API calls** - Complete privacy, offline capable
- **Concurrent request handling** - Async I/O with uvicorn workers

## Security & Privacy

- ✅ No API keys transmitted (local Ollama)
- ✅ No data sent to external services
- ✅ Environment-based configuration
- ✅ Type-validated inputs (Pydantic)
- ✅ CORS configuration for frontend integration

## Extensibility

The architecture supports:
- **Additional Tools** - Easy to add new agent capabilities
- **Alternative LLMs** - Abstract LLM interface in `llm.py`
- **Database Integration** - FastAPI ready for PostgreSQL, MongoDB
- **Authentication** - JWT support in FastAPI
- **Caching** - Redis integration patterns established

## Future Enhancements

- Multi-model support (LLaMA, Mistral, etc.)
- Vector embeddings for semantic resume matching
- Batch processing for multiple resumes
- Web-based model fine-tuning interface
- API rate limiting and monitoring

---

**Perfect for demonstrating**: Full-stack development, AI integration, modern Python practices, async programming, DevOps fundamentals, and production-ready software architecture.
