import pytest
from unittest.mock import AsyncMock, patch
from app.agents.researcher import run_research_batch, _BudgetManager
from app.models import SubQuestion, ResearchNote, SourceFinding, Citation, ResearchSynthesis
from app.tools.tavily_search import SearchToolError
from pydantic import HttpUrl

@pytest.fixture
def sample_subquestions():
    return [
        SubQuestion(
            id="sq1",
            question="What is the current inflation rate?",
            priority=1,
            search_queries=["us inflation rate 2025", "eurozone inflation"]
        )
    ]

@pytest.mark.asyncio
async def test_budget_manager_deduping():
    budget = _BudgetManager()
    finding = SourceFinding(
        title="Test Source",
        url=HttpUrl("https://example.com/item"),
        snippet="Test snippet",
        source_name="example.com"
    )
    
    # First acceptance
    accepted, reason = await budget.try_accept("sq1", finding)
    assert accepted is True
    assert reason == "accepted"
    
    # Second acceptance of same URL (different case/slashes handled by router/normalize)
    # Researcher.py uses normalize_url
    accepted2, reason2 = await budget.try_accept("sq1", finding)
    assert accepted2 is False
    assert reason2 == "deduped"

@pytest.mark.asyncio
async def test_run_research_batch_success(sample_subquestions):
    # Mock search results and synthesis
    mock_findings = [
        SourceFinding(
            title="Current US inflation rate data 2025",
            url=HttpUrl("https://news.com/inflation-2025"),
            snippet="The current inflation rate provides value to central banks.",
            source_name="news.com"
        )
    ]
    
    async def mock_emit(event, data):
        pass

    with patch("app.agents.researcher.search_web_parallel", new_callable=AsyncMock) as mock_search, \
         patch("app.agents.researcher.call_openai_typed", new_callable=AsyncMock) as mock_llm:
        
        mock_search.return_value = mock_findings
        mock_llm.return_value = ResearchSynthesis(
            evidence_bullets=["Bullet 1", "Bullet 2", "Bullet 3", "Bullet 4"],
            contradictions=[],
            gaps=[]
        )
        
        notes, citations, errors = await run_research_batch(
            sub_questions=sample_subquestions,
            emit_event=mock_emit,
            query="inflation"
        )
        
        assert "sq1" in notes
        assert len(citations) >= 1
        assert len(errors) == 0
        assert notes["sq1"].evidence_bullets[0] == "Bullet 1"

@pytest.mark.asyncio
async def test_research_failure_handling(sample_subquestions):
    async def mock_emit(event, data):
        pass

    with patch("app.agents.researcher.search_web_parallel", side_effect=SearchToolError("Search Timeout")):
        notes, citations, errors = await run_research_batch(
            sub_questions=sample_subquestions,
            emit_event=mock_emit,
            query="inflation"
        )
        
        # Should complete even with errors
        assert "sq1" in notes
        assert len(errors) > 0
        assert "Search Timeout" in errors[0]["detail"]
