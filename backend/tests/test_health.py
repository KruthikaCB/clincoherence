"""
Smoke tests for the health check endpoint.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient) -> None:
    """GET /health should return 200 with status ok."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_health_check_response_structure(client: AsyncClient) -> None:
    """GET /health response should contain exactly the expected keys."""
    response = await client.get("/health")
    data = response.json()
    assert set(data.keys()) == {"status"}

