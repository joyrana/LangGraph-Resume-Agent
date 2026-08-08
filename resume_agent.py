from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from config import get_settings
from document_service import create_resume_docx
from llm import get_ollama_llm


class ResumeState(TypedDict, total=False):
    session_id: str
    original_resume_text: str
    job_description: str
    company_details: str
    candidate_name: str | None
    feedback: str
    approved: bool
    ats_score: int
    analysis: str
    draft_resume: str
    revision_notes: str
    final_file_path: str


class ResumeAgent:
    def __init__(self) -> None:
        self._llm = None
        self._graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(ResumeState)
        graph.add_node("analyze", self._analyze_node)
        graph.add_node("draft", self._draft_node)
        graph.add_node("finalize", self._finalize_node)
        graph.set_entry_point("analyze")
        graph.add_edge("analyze", "draft")
        graph.add_conditional_edges(
            "draft",
            self._should_finalize,
            {"finalize": "finalize", "end": END},
        )
        graph.add_edge("finalize", END)
        return graph.compile()

    async def _get_llm(self):
        if self._llm is None:
            self._llm = await get_ollama_llm()
        return self._llm

    async def _analyze_node(self, state: ResumeState) -> ResumeState:
        llm = await self._get_llm()
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a resume ATS analyst. Return JSON with keys: "
                    "ats_score (0-100), analysis (short paragraph), keywords (array), gaps (array)."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Resume:\n{state['original_resume_text']}\n\n"
                    f"Job Description:\n{state['job_description']}\n\n"
                    f"Company Details:\n{state['company_details']}"
                ),
            },
        ]
        response = await llm.invoke(messages, temperature=0.2, max_tokens=700)
        payload = self._parse_json(response)
        state["ats_score"] = int(payload.get("ats_score", 65))
        state["analysis"] = payload.get("analysis", response)
        return state

    async def _draft_node(self, state: ResumeState) -> ResumeState:
        llm = await self._get_llm()
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert resume writer optimizing for ATS. "
                    "Rewrite the resume in concise sections: Summary, Skills, Experience, Education, Projects. "
                    "Preserve factual accuracy. Return plain text with clear headings."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Candidate name: {state.get('candidate_name') or 'Not provided'}\n\n"
                    f"Current resume:\n{state['original_resume_text']}\n\n"
                    f"Job description:\n{state['job_description']}\n\n"
                    f"Company details:\n{state['company_details']}\n\n"
                    f"ATS analysis:\n{state.get('analysis', '')}\n\n"
                    f"Human feedback:\n{state.get('feedback', '')}"
                ),
            },
        ]
        response = await llm.invoke(messages, temperature=0.4, max_tokens=1800)
        state["draft_resume"] = response.strip()
        state["revision_notes"] = "Resume draft generated with ATS-aligned keywords and tailored phrasing."
        return state

    async def _finalize_node(self, state: ResumeState) -> ResumeState:
        settings = get_settings()
        output_dir = settings.generated_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"resume_{state['session_id']}.docx"
        file_path = output_dir / file_name
        create_resume_docx(state["draft_resume"], file_path)
        state["final_file_path"] = str(file_path)
        return state

    def _should_finalize(self, state: ResumeState) -> str:
        return "finalize" if state.get("approved") else "end"

    def _parse_json(self, text: str) -> dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}

    async def generate(self, state: ResumeState) -> ResumeState:
        return await self._graph.ainvoke(state)


_agent: ResumeAgent | None = None


async def get_resume_agent() -> ResumeAgent:
    global _agent
    if _agent is None:
        _agent = ResumeAgent()
    return _agent
