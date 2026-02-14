import pytest
from unittest.mock import AsyncMock, patch
from app.agents.planner import run_planner
from app.models import Plan, SubQuestion

@pytest.mark.asyncio
async def test_run_planner_success():
    # Mock response from OpenAI
    mock_plan = Plan(
        sub_questions=[
            SubQuestion(
                id="sq1",
                question="What is inflation?",
                priority=1,
                search_queries=["inflation causes", "inflation definition"]
            ),
            SubQuestion(
                id="sq2",
                question="How do central banks respond?",
                priority=2,
                search_queries=["monetary policy inflation", "interest rates"]
            ),
            SubQuestion(
                id="sq3",
                question="What is the 2026 outlook?",
                priority=3,
                search_queries=["inflation forecast 2026", "global economy outlook"]
            )
        ],
        assumptions=["Standard economic model used."]
    )

    with patch("app.agents.planner.call_openai_typed", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_plan
        
        result = await run_planner(
            query="Tell me about inflation",
            history=[],
            shared_memory={}
        )
        
        assert isinstance(result, Plan)
        assert len(result.sub_questions) >= 3
        assert result.sub_questions[0].id == "sq1"
        assert "inflation" in result.sub_questions[0].question.lower()

@pytest.mark.asyncio
async def test_run_planner_fallback():
    # Simulate a failure in OpenAI call
    with patch("app.agents.planner.call_openai_typed", side_effect=Exception("API Error")):
        result = await run_planner(
            query="Test query",
            history=[],
            shared_memory={}
        )
        
        # Should return fallback plan
        assert isinstance(result, Plan)
        assert len(result.sub_questions) == 3
        assert "Fallback plan" in result.assumptions[0]

@pytest.mark.asyncio
async def test_run_planner_with_history():
    mock_plan = Plan(
        sub_questions=[
            SubQuestion(id="sq1", question="Q1", priority=1, search_queries=["q1a", "q1b"]),
            SubQuestion(id="sq2", question="Q2", priority=2, search_queries=["q2a", "q2b"]),
            SubQuestion(id="sq3", question="Q3", priority=3, search_queries=["q3a", "q3b"]),
        ],
        assumptions=["SKIP_WEB_RESEARCH"]
    )

    with patch("app.agents.planner.call_openai_typed", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_plan
        
        result = await run_planner(
            query="Follow up query",
            history=[{"role": "user", "content": "Initial query"}, {"role": "assistant", "content": "Initial answer"}],
            shared_memory={}
        )
        
        assert "SKIP_WEB_RESEARCH" in result.assumptions
