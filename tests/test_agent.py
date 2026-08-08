"""
Integration tests for the ReAct Agent
"""
import pytest
import asyncio
from agent import get_react_agent


@pytest.mark.asyncio
async def test_agent_invoke():
    """Test basic agent invocation"""
    agent = await get_react_agent()
    result = await agent.invoke("What is 10 plus 5?")
    
    assert "input" in result
    assert "output" in result
    assert "steps" in result
    assert result["steps"] > 0


@pytest.mark.asyncio
async def test_agent_max_steps():
    """Test agent respects max steps limit"""
    agent = await get_react_agent()
    agent.max_steps = 2
    
    result = await agent.invoke("Perform multiple calculations")
    assert result["steps"] <= agent.max_steps


@pytest.mark.asyncio
async def test_agent_extraction():
    """Test section extraction from response"""
    test_text = """Thought: I need to calculate this
Action: calculator
Action Input: {"operation": "add", "a": 5, "b": 3}"""
    
    from agent import ReActAgent
    assert ReActAgent._extract_section(test_text, "Thought") == "I need to calculate this"
    assert ReActAgent._extract_section(test_text, "Action") == "calculator"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
