#!/bin/bash
# Quick start script for the ReAct Agent

set -e

echo "🚀 Starting Grok ReAct Agent..."
echo ""

# Check if .env exists
if [ ! -f ".env" ]; then
    echo "❌ Error: .env file not found"
    echo "Please ensure .env file exists in the project root"
    exit 1
fi

# Run the FastAPI server
echo "📡 Starting FastAPI server on http://localhost:8000"
echo "📚 API docs available at http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
