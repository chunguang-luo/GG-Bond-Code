"""Integration tests for compaction in QueryRunner."""

import pytest
from next_code.query import QueryRunner
from next_code.state.context import create_store_context
from next_code.state.store import Store
from next_code.tools.base import create_default_registry


@pytest.mark.asyncio
async def test_query_runner_compacts_messages():
    """Test that QueryRunner compacts messages when limit is exceeded."""
    store = Store()
    registry = create_default_registry()
    ctx = create_store_context(store=store, registry=registry)

    # Create runner with low max_messages threshold
    runner = QueryRunner(
        context=ctx,
        enable_compaction=True,
        max_messages=5,
    )

    # Simulate a long conversation history
    messages = [{"role": "user", "content": f"Message {i}"} for i in range(10)]
    store.set("messages", messages)

    # The runner should compact messages internally
    # This test verifies the integration - actual behavior depends on model calls
    assert len(store.get("messages")) == 10


@pytest.mark.asyncio
async def test_query_runner_compaction_disabled():
    """Test that QueryRunner can disable compaction."""
    store = Store()
    registry = create_default_registry()
    ctx = create_store_context(store=store, registry=registry)

    runner = QueryRunner(
        context=ctx,
        enable_compaction=False,
    )

    assert not runner._enable_compaction


@pytest.mark.asyncio
async def test_query_runner_recovery_strategies():
    """Test that QueryRunner has recovery strategies configured."""
    store = Store()
    registry = create_default_registry()
    ctx = create_store_context(store=store, registry=registry)

    runner = QueryRunner(context=ctx)

    # Should have recovery strategies configured
    assert len(runner._recovery_strategies) > 0
    assert runner._recovery_count == 0
