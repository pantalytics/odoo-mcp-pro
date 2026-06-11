"""Tests for performance optimization module."""

import asyncio
import os
import time
from unittest.mock import Mock, patch

import pytest

from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.performance import (
    ConnectionPool,
    PerformanceManager,
    PerformanceMonitor,
)


class TestConnectionPool:
    """Test ConnectionPool functionality."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = Mock(spec=OdooConfig)
        config.url = os.getenv("ODOO_URL", "http://localhost:8069")
        return config

    def test_connection_pool_creation(self, mock_config):
        """Test creating a connection pool."""
        pool = ConnectionPool(mock_config, max_connections=5)

        assert pool.max_connections == 5
        assert pool.config == mock_config
        assert len(pool._connections) == 0

    @patch("mcp_server_odoo.performance.ServerProxy")
    def test_get_connection(self, mock_proxy, mock_config):
        """Test getting a connection from pool."""
        pool = ConnectionPool(mock_config)

        # Get a connection
        pool.get_connection("/xmlrpc/2/common")

        # Should create a new connection
        mock_proxy.assert_called_once()
        stats = pool.get_stats()
        assert stats["connections_created"] == 1
        assert stats["connections_reused"] == 0

        # Get same endpoint again (should reuse)
        pool.get_connection("/xmlrpc/2/common")

        stats = pool.get_stats()
        assert stats["connections_created"] == 1
        assert stats["connections_reused"] == 1

    @patch("mcp_server_odoo.performance.ServerProxy")
    def test_connection_pool_max_limit(self, mock_proxy, mock_config):
        """Test connection pool respects max connections."""
        pool = ConnectionPool(mock_config, max_connections=2)

        # Create max connections
        pool.get_connection("/endpoint1")
        pool.get_connection("/endpoint2")

        stats = pool.get_stats()
        assert stats["active_connections"] == 2

        # Creating another should remove oldest
        pool.get_connection("/endpoint3")

        stats = pool.get_stats()
        assert stats["active_connections"] == 2
        assert stats["connections_closed"] == 1

    def test_connection_pool_clear(self, mock_config):
        """Test clearing connection pool."""
        pool = ConnectionPool(mock_config)

        # Add some connections
        with patch("mcp_server_odoo.performance.ServerProxy"):
            pool.get_connection("/endpoint1")
            pool.get_connection("/endpoint2")

        # Clear pool
        pool.clear()

        stats = pool.get_stats()
        assert stats["active_connections"] == 0
        assert stats["connections_closed"] == 2


class TestPerformanceMonitor:
    """Test PerformanceMonitor functionality."""

    def test_track_operation(self):
        """Test tracking operation performance."""
        monitor = PerformanceMonitor()

        # Track an operation
        with monitor.track_operation("test_op"):
            time.sleep(0.01)  # Simulate work

        stats = monitor.get_stats()
        assert "test_op" in stats["operations"]
        assert stats["operations"]["test_op"]["count"] == 1
        assert stats["operations"]["test_op"]["avg_ms"] > 0

    def test_multiple_operations(self):
        """Test tracking multiple operations."""
        monitor = PerformanceMonitor()

        # Track multiple operations
        for _ in range(5):
            with monitor.track_operation("op1"):
                time.sleep(0.001)

        for _ in range(3):
            with monitor.track_operation("op2"):
                time.sleep(0.002)

        stats = monitor.get_stats()
        assert stats["operations"]["op1"]["count"] == 5
        assert stats["operations"]["op2"]["count"] == 3
        assert stats["operations"]["op2"]["avg_ms"] > stats["operations"]["op1"]["avg_ms"]


class TestPerformanceManager:
    """Test PerformanceManager functionality."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = Mock(spec=OdooConfig)
        config.url = os.getenv("ODOO_URL", "http://localhost:8069")
        return config

    def test_performance_manager_creation(self, mock_config):
        """Test creating performance manager."""
        manager = PerformanceManager(mock_config)

        assert manager.config == mock_config
        assert manager.field_cache is not None
        assert manager.record_cache is not None
        assert manager.permission_cache is not None
        assert manager.connection_pool is not None
        assert manager.request_optimizer is not None
        assert manager.monitor is not None

    def test_cache_key_generation(self, mock_config):
        """Test cache key generation."""
        manager = PerformanceManager(mock_config)

        # Simple key
        key = manager.cache_key("test", model="res.partner", id=1)
        assert key == "test:id:1:model:res.partner"

        # Complex key with list
        key = manager.cache_key("test", fields=["name", "email"], model="res.partner")
        assert "model:res.partner" in key
        assert "fields:" in key

        # Test key with None fields
        key = manager.cache_key("record", model="res.partner", id=1, fields=None)
        print(f"Key with fields=None: {key}")

        # Test invalidation pattern
        pattern = "record:model:res.partner:id:1:*"
        print(f"Invalidation pattern: {pattern}")
        print(f"Pattern matches key: {key.startswith(pattern.rstrip('*'))}")

    def test_field_caching(self, mock_config):
        """Test field definition caching."""
        manager = PerformanceManager(mock_config)

        fields = {
            "name": {"type": "char", "string": "Name"},
            "email": {"type": "char", "string": "Email"},
        }

        # Cache fields
        manager.cache_fields("res.partner", fields)

        # Get cached fields
        cached = manager.get_cached_fields("res.partner")
        assert cached == fields

    def test_record_caching(self, mock_config):
        """Test record caching."""
        manager = PerformanceManager(mock_config)

        record = {"id": 1, "name": "Test Partner", "email": "test@example.com"}

        # Cache record
        manager.cache_record("res.partner", record, fields=["name", "email"])

        # Get cached record
        cached = manager.get_cached_record("res.partner", 1, fields=["name", "email"])
        assert cached == record

    def test_record_cache_invalidation(self, mock_config):
        """Test record cache invalidation."""
        manager = PerformanceManager(mock_config)

        # Cache some records with same fields parameter as when retrieving
        manager.cache_record("res.partner", {"id": 1, "name": "Partner 1"}, fields=None)
        manager.cache_record("res.partner", {"id": 2, "name": "Partner 2"}, fields=None)
        manager.cache_record("res.users", {"id": 1, "name": "User 1"}, fields=None)

        # Verify they're cached
        assert manager.get_cached_record("res.partner", 1, fields=None) is not None
        assert manager.get_cached_record("res.partner", 2, fields=None) is not None

        # Invalidate specific record
        manager.invalidate_record_cache("res.partner", 1)
        assert manager.get_cached_record("res.partner", 1, fields=None) is None
        assert manager.get_cached_record("res.partner", 2, fields=None) is not None

        # Invalidate all partner records
        manager.invalidate_record_cache("res.partner")
        assert manager.get_cached_record("res.partner", 2, fields=None) is None
        assert manager.get_cached_record("res.users", 1, fields=None) is not None

    def test_permission_caching(self, mock_config):
        """Test permission caching."""
        manager = PerformanceManager(mock_config)

        # Cache permission
        manager.cache_permission("res.partner", "read", user_id=2, allowed=True)

        # Get cached permission
        cached = manager.get_cached_permission("res.partner", "read", user_id=2)
        assert cached is True

    def test_get_comprehensive_stats(self, mock_config):
        """Test getting comprehensive performance stats."""
        manager = PerformanceManager(mock_config)

        # Do some operations
        manager.cache_fields("res.partner", {"name": {"type": "char"}})
        manager.cache_record("res.partner", {"id": 1, "name": "Test"})
        manager.cache_permission("res.partner", "read", 2, True)

        with manager.monitor.track_operation("test_op"):
            time.sleep(0.001)

        # Get stats
        stats = manager.get_stats()

        assert "caches" in stats
        assert "field_cache" in stats["caches"]
        assert "record_cache" in stats["caches"]
        assert "permission_cache" in stats["caches"]
        assert "connection_pool" in stats
        assert "performance" in stats

    def test_clear_all_caches(self, mock_config):
        """Test clearing all caches."""
        manager = PerformanceManager(mock_config)

        # Add data to caches
        manager.cache_fields("res.partner", {"name": {"type": "char"}})
        manager.cache_record("res.partner", {"id": 1, "name": "Test"})
        manager.cache_permission("res.partner", "read", 2, True)

        # Clear all
        manager.clear_all_caches()

        # Verify all caches are empty
        assert manager.get_cached_fields("res.partner") is None
        assert manager.get_cached_record("res.partner", 1) is None
        assert manager.get_cached_permission("res.partner", "read", 2) is None


class TestPerformanceIntegration:
    """Integration tests for performance features."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = Mock(spec=OdooConfig)
        config.url = os.getenv("ODOO_URL", "http://localhost:8069")
        return config

    @pytest.mark.asyncio
    async def test_concurrent_cache_access(self, mock_config):
        """Test cache with concurrent access."""
        manager = PerformanceManager(mock_config)

        async def cache_operation(i):
            """Perform cache operations."""
            # Write
            manager.cache_record("res.partner", {"id": i, "name": f"Partner {i}"}, fields=None)

            # Read
            for _ in range(10):
                record = manager.get_cached_record("res.partner", i, fields=None)
                assert record is not None
                await asyncio.sleep(0.001)

        # Run concurrent operations (start from 1 to avoid ID 0)
        tasks = [cache_operation(i) for i in range(1, 11)]
        await asyncio.gather(*tasks)

        # Check stats
        stats = manager.record_cache.get_stats()
        assert stats["hits"] >= 90  # At least 90 hits from 10 tasks * 10 reads
        assert stats["total_entries"] == 10

    def test_performance_under_load(self, mock_config):
        """Test performance manager under load."""
        manager = PerformanceManager(mock_config)

        start_time = time.time()

        # Simulate heavy usage
        for i in range(1000):
            # Cache operations
            manager.cache_record("res.partner", {"id": i, "name": f"Partner {i}"})

            # Some cache hits
            if i > 100:
                manager.get_cached_record("res.partner", i - 100)

            # Track operations
            with manager.monitor.track_operation(f"op_{i % 10}"):
                time.sleep(0.0001)

        duration = time.time() - start_time

        # Should complete reasonably fast
        assert duration < 5.0  # 5 seconds for 1000 operations

        # Check cache is working
        stats = manager.record_cache.get_stats()
        assert stats["hit_rate"] > 0.8  # Good hit rate
