"""
ReAct Agent implementation using LangGraph
"""
import json
import re
from typing import Any
from enum import Enum
from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict
from llm import get_ollama_llm
from tools import execute_tool, get_tools_description, TOOLS


class AgentState(TypedDict):
    """State for the ReAct agent"""
    messages: list[dict]
    thought: str
    action: str
    action_input: dict
    observation: str
    final_answer: str
    step_count: int


class ReActAgent:
    """ReAct (Reasoning + Acting) Agent with LangGraph"""
    
    def __init__(self):
        self.llm = None
        self.graph = self._build_graph()
        self.max_steps = 10
    
    def _build_graph(self) -> Any:
        """Build the agent graph"""
        graph = StateGraph(AgentState)
        
        graph.add_node("think", self._think_node)
        graph.add_node("act", self._act_node)
        graph.add_node("observe", self._observe_node)
        graph.add_node("answer", self._answer_node)
        
        graph.set_entry_point("think")
        
        graph.add_edge("think", "act")
        graph.add_conditional_edges(
            "act",
            self._should_continue,
            {
                "continue": "observe",
                "end": "answer",
            }
        )
        graph.add_edge("observe", "think")
        graph.add_edge("answer", END)
        
        return graph.compile()
    
    async def _think_node(self, state: AgentState) -> AgentState:
        """Think node - generate thought and action"""
        if self.llm is None:
            self.llm = await get_ollama_llm()
        
        # Build prompt with tools info
        tools_desc = get_tools_description()
        messages_for_llm = [
            {
                "role": "system",
                "content": f"""You are a helpful ReAct agent. Follow this format exactly:

Thought: Your reasoning about what to do
Action: The tool to use, must be one of: {', '.join(TOOLS.keys())}
Action Input: The input to the tool in JSON format

{tools_desc}

If you have the final answer, use:
Thought: I have the final answer
Action: FINAL_ANSWER
Action Input: {{"answer": "your answer here"}}"""
            }
        ]
        
        for msg in state["messages"]:
            messages_for_llm.append(msg)
        
        if state["observation"]:
            messages_for_llm.append({
                "role": "assistant",
                "content": f"Observation: {state['observation']}"
            })
        
        response = await self.llm.invoke(messages_for_llm, temperature=0.7)
        
        # Parse response
        thought = self._extract_section(response, "Thought")
        action = self._extract_section(response, "Action")
        action_input_str = self._extract_section(response, "Action Input")
        
        try:
            action_input = json.loads(action_input_str)
        except json.JSONDecodeError:
            action_input = {"raw_input": action_input_str}
        
        state["thought"] = thought
        state["action"] = action
        state["action_input"] = action_input
        
        return state
    
    async def _act_node(self, state: AgentState) -> AgentState:
        """Act node - execute the action"""
        action = state["action"].strip()
        action_input = state["action_input"]
        
        if action == "FINAL_ANSWER":
            state["final_answer"] = action_input.get("answer", "")
        else:
            result = await execute_tool(action, **action_input)
            state["observation"] = json.dumps(result)
        
        state["step_count"] += 1
        return state
    
    def _should_continue(self, state: AgentState) -> str:
        """Determine if we should continue or end"""
        if state["action"] == "FINAL_ANSWER" or state["step_count"] >= self.max_steps:
            return "end"
        return "continue"
    
    async def _observe_node(self, state: AgentState) -> AgentState:
        """Observe node - process observation"""
        return state
    
    async def _answer_node(self, state: AgentState) -> AgentState:
        """Answer node - prepare final answer"""
        if not state["final_answer"]:
            state["final_answer"] = state["observation"]
        return state
    
    @staticmethod
    def _extract_section(text: str, section: str) -> str:
        """Extract a section from the response text"""
        pattern = rf"{section}:\s*(.+?)(?:(?=\n[A-Z][a-z]+:)|$)"
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return ""
    
    async def invoke(self, user_input: str) -> dict:
        """
        Run the agent with user input
        
        Args:
            user_input: The user's question or task
            
        Returns:
            Agent's final answer and execution trace
        """
        initial_state: AgentState = {
            "messages": [
                {"role": "user", "content": user_input}
            ],
            "thought": "",
            "action": "",
            "action_input": {},
            "observation": "",
            "final_answer": "",
            "step_count": 0,
        }
        
        result = await self.graph.ainvoke(initial_state)
        
        return {
            "input": user_input,
            "output": result["final_answer"],
            "steps": result["step_count"],
        }


# Global instance
_agent: ReActAgent | None = None


async def get_react_agent() -> ReActAgent:
    """Get or create ReAct agent instance"""
    global _agent
    if _agent is None:
        _agent = ReActAgent()
    return _agent
