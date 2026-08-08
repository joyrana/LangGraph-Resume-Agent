"""
Tests for tools module
"""
import pytest
from tools import execute_calculator, execute_search, execute_web_fetch


@pytest.mark.asyncio
async def test_calculator_add():
    """Test calculator addition"""
    result = await execute_calculator("add", 5, 3)
    assert result["result"] == 8


@pytest.mark.asyncio
async def test_calculator_divide_by_zero():
    """Test calculator division by zero"""
    result = await execute_calculator("divide", 10, 0)
    assert "error" in result


@pytest.mark.asyncio
async def test_search():
    """Test search tool"""
    result = await execute_search("python")
    assert "results" in result
    assert "query" in result


@pytest.mark.asyncio
async def test_web_fetch():
    """Test web fetch tool"""
    result = await execute_web_fetch("https://example.com")
    assert result["url"] == "https://example.com"
    assert result["status"] == "success"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
