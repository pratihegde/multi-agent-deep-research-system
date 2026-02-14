import pytest
from unittest.mock import AsyncMock, patch
from app.agents.writer import stream_report_chunks
from app.models import Citation, FinalReport
from pydantic import HttpUrl

@pytest.mark.asyncio
async def test_writer_success():
    async def mock_emit(event, data):
        pass

    mock_summary = FinalReport(
        executive_summary="Summary text",
        report="Body text",
        key_takeaways=["Takeaway 1"],
        limitations="Limited data"
    )

    with patch("app.agents.writer.stream_openai_text") as mock_stream, \
         patch("app.agents.writer.call_openai_typed", new_callable=AsyncMock) as mock_llm:
        
        # Mock generator for stream
        async def mock_tokens():
            yield "Body "
            yield "text"
        mock_stream.return_value = mock_tokens()
        mock_llm.return_value = mock_summary

        result = await stream_report_chunks(
            query="Test query",
            research_notes={},
            citations=[Citation(title="C1", url=HttpUrl("https://c1.com"), source_name="c1")],
            history=[],
            shared_memory={},
            quality_score=None,
            quality_feedback=[],
            rewrite_iteration=0,
            emit_event=mock_emit
        )

        assert result.executive_summary == "Summary text"
        assert "Body text" in result.report
        assert result.key_takeaways[0] == "Takeaway 1"

@pytest.mark.asyncio
async def test_writer_fallback():
    async def mock_emit(event, data):
        pass

    # Force error in stream or summary
    with patch("app.agents.writer.stream_openai_text", side_effect=Exception("API Down")), \
         patch("app.agents.writer.call_openai_typed", side_effect=Exception("API Down")):
        
        result = await stream_report_chunks(
            query="Test query",
            research_notes={"sq1": {"evidence_bullets": ["B1"], "findings": []}},
            citations=[],
            history=[],
            shared_memory={},
            quality_score=None,
            quality_feedback=[],
            rewrite_iteration=0,
            emit_event=mock_emit
        )

        # Should use _fallback_report
        assert "fallback" in result.executive_summary.lower()
        assert "SQ1" in result.report
