import json
import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from app.main import app
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

@pytest.mark.asyncio
async def test_chat_endpoint_validation():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Test too short message
        response = await ac.post("/chat", json={"message": "hi"})
    assert response.status_code == 422 # Pydantic min_length=3

@pytest.mark.asyncio
async def test_chat_sse_stream_structure():
    # We don't want to run the full LLM workflow in integration tests to save time/cost.
    # We'll mock the workflow built in sse.py.
    
    mock_workflow = AsyncMock()
    
    mock_final_report = AsyncMock()
    mock_final_report.executive_summary = "Summary"
    mock_final_report.report = "Report Content"
    mock_final_report.key_takeaways = ["T1"]
    mock_final_report.limitations = "None"

    mock_plan = AsyncMock()
    mock_plan.sub_questions = []

    mock_quality = AsyncMock()
    mock_quality.score = 90

    mock_final_state = {
        "final_report": mock_final_report,
        "plan": mock_plan,
        "citations": [],
        "quality": mock_quality,
        "metadata": {"timings_ms": {}},
        "shared_memory": {},
        "history": [],
        "refinement_used": False
    }
    mock_workflow.ainvoke.return_value = mock_final_state

    with patch("app.services.sse.build_workflow", return_value=mock_workflow):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/chat", json={"message": "Test research query"})
            
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            
            # Read first few chunks
            events = []
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    events.append(line.replace("event: ", ""))
                if len(events) >= 2: # Stop after thread_id and maybe something else
                    break
            
            assert "thread_id" in events
