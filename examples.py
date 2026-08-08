"""
Example usage of the ReAct Agent
"""
import asyncio
from agent import get_react_agent


async def main():
    """Run example queries"""
    agent = await get_react_agent()
    
    examples = [
        "What is 15 times 8?",
        "Search for LangGraph documentation",
        "Find information about FastAPI"
    ]
    
    for query in examples:
        print(f"\n{'='*60}")
        print(f"Query: {query}")
        print('='*60)
        
        result = await agent.invoke(query)
        print(f"Answer: {result['output']}")
        print(f"Steps: {result['steps']}")


if __name__ == "__main__":
    asyncio.run(main())
