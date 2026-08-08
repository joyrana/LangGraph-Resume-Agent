"""
CLI interface for the ReAct Agent
"""
import asyncio
import sys
from agent import get_react_agent


async def main():
    """Run the agent CLI"""
    print("=" * 60)
    print("Grok ReAct Agent CLI")
    print("=" * 60)
    print("\nType 'exit' to quit\n")
    
    agent = await get_react_agent()
    
    while True:
        try:
            user_input = input("You: ").strip()
            
            if user_input.lower() in ["exit", "quit", "q"]:
                print("Goodbye!")
                break
            
            if not user_input:
                continue
            
            print("\nAgent is thinking...\n")
            result = await agent.invoke(user_input)
            
            print(f"Agent: {result['output']}")
            print(f"(Completed in {result['steps']} steps)\n")
            
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"Error: {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
