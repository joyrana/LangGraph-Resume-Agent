"""
Tools for the ReAct Agent
"""
import json
from typing import Any


# Tool registry
TOOLS = {
    "calculator": {
        "description": "Useful for math calculations",
        "params": {
            "operation": "str - mathematical operation (add, subtract, multiply, divide)",
            "a": "float - first number",
            "b": "float - second number",
        }
    },
    "search": {
        "description": "Search for information",
        "params": {
            "query": "str - search query",
            "max_results": "int - maximum results to return (default: 5)",
        }
    },
    "web_fetch": {
        "description": "Fetch content from a URL",
        "params": {
            "url": "str - URL to fetch",
        }
    },
}


async def execute_calculator(operation: str, a: float, b: float) -> dict:
    """Execute calculator operations"""
    try:
        if operation == "add":
            result = a + b
        elif operation == "subtract":
            result = a - b
        elif operation == "multiply":
            result = a * b
        elif operation == "divide":
            if b == 0:
                return {"error": "Division by zero"}
            result = a / b
        else:
            return {"error": f"Unknown operation: {operation}"}
        
        return {"result": result, "operation": operation, "a": a, "b": b}
    except Exception as e:
        return {"error": str(e)}


async def execute_search(query: str, max_results: int = 5) -> dict:
    """Simulated search function"""
    # In a real implementation, this would call a search API
    return {
        "query": query,
        "results": [
            {"title": f"Result {i+1}", "url": f"https://example.com/{i+1}"}
            for i in range(min(max_results, 3))
        ],
        "total": min(max_results, 3)
    }


async def execute_web_fetch(url: str) -> dict:
    """Simulated web fetch function"""
    # In a real implementation, this would fetch actual content
    return {
        "url": url,
        "content": f"Mock content from {url}",
        "status": "success"
    }


async def execute_tool(tool_name: str, **kwargs) -> dict:
    """
    Execute a tool with given arguments
    
    Args:
        tool_name: Name of the tool to execute
        **kwargs: Tool arguments
        
    Returns:
        Tool execution result
    """
    if tool_name == "calculator":
        return await execute_calculator(**kwargs)
    elif tool_name == "search":
        return await execute_search(**kwargs)
    elif tool_name == "web_fetch":
        return await execute_web_fetch(**kwargs)
    else:
        return {"error": f"Unknown tool: {tool_name}"}


def get_tools_description() -> str:
    """Get description of available tools for the agent"""
    description = "Available tools:\n\n"
    for tool_name, tool_info in TOOLS.items():
        description += f"- {tool_name}: {tool_info['description']}\n"
        description += "  Parameters:\n"
        for param_name, param_desc in tool_info['params'].items():
            description += f"    - {param_desc}\n"
        description += "\n"
    return description
