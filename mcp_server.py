"""
MCP Server implementation for the agent
"""
from fastmcp import Server, Tool
from agent import get_react_agent
import json


def create_mcp_server() -> Server:
    """Create FastMCP server with agent tools"""
    server = Server("langgraph-resume-agent-mcp")
    
    @server.tool()
    async def invoke_agent(query: str) -> dict:
        """
        Invoke the ReAct agent with a query
        
        Args:
            query: The user's query or task
            
        Returns:
            Agent's response with reasoning trace
        """
        agent = await get_react_agent()
        result = await agent.invoke(query)
        return result
    
    return server
