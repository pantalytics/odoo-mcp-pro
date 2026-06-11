"""Tests for cache and request optimizer (mcp_server_odoo.performance_cache)."""

import time
from datetime import datetime, timedelta

from mcp_server_odoo.performance_cache import (
    Cache,
    CacheEntry,
    RequestOptimizer,
)


class TestCacheEntry:
    """Test CacheEntry functionality."""

    def test_cache_entry_creation(self):
        """Test creating a cache entry."""
        now = datetime.now()
        entry = CacheEntry(
            key="test_key",
            value={"data": "test"},
            created_at=now,
            accessed_at=now,
            ttl_seconds=300,
            hit_count=0,
            size_bytes=100,
        )

        assert entry.key == "test_key"
        assert entry.value == {"data": "test"}
        assert entry.ttl_seconds == 300
        assert entry.hit_count == 0
        assert not entry.is_expired()

    def test_cache_entry_expiration(self):
        """Test cache entry expiration."""
        # Create an entry that's already expired
        old_time = datetime.now() - timedelta(seconds=600)
        entry = CacheEntry(
            key="test_key",
            value="test_value",
            created_at=old_time,
            accessed_at=old_time,
            ttl_seconds=300,
        )

        assert entry.is_expired()

    def test_cache_entry_access(self):
        """Test accessing a cache entry."""
        entry = CacheEntry(
            key="test_key",
            value="test_value",
            created_at=datetime.now(),
            accessed_at=datetime.now(),
            ttl_seconds=300,
        )

        original_access_time = entry.accessed_at
        original_hit_count = entry.hit_count

        # Access the entry
        time.sleep(0.01)  # Small delay to ensure time difference
        entry.access()

        assert entry.hit_count == original_hit_count + 1
        assert entry.accessed_at > original_access_time


class TestCache:
    """Test Cache functionality."""

    def test_cache_put_and_get(self):
        """Test basic cache put and get operations."""
        cache = Cache(max_size=10, max_memory_mb=1)

        # Put a value
        cache.put("key1", {"data": "value1"}, ttl_seconds=300)

        # Get the value
        value = cache.get("key1")
        assert value == {"data": "value1"}

        # Check stats
        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 0
        assert stats["total_entries"] == 1

    def test_cache_miss(self):
        """Test cache miss."""
        cache = Cache()

        # Try to get non-existent key
        value = cache.get("non_existent")
        assert value is None

        stats = cache.get_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 1

    def test_cache_expiration(self):
        """Test cache entry expiration."""
        cache = Cache()

        # Put a value with very short TTL
        cache.put("key1", "value1", ttl_seconds=0)

        # Try to get it (should be expired)
        time.sleep(0.01)
        value = cache.get("key1")
        assert value is None

        stats = cache.get_stats()
        assert stats["expired_evictions"] == 1

    def test_cache_lru_eviction(self):
        """Test LRU eviction when cache is full."""
        cache = Cache(max_size=3)

        # Fill the cache
        cache.put("key1", "value1")
        cache.put("key2", "value2")
        cache.put("key3", "value3")

        # Access key1 and key2 to make them more recently used
        cache.get("key1")
        cache.get("key2")

        # Add a new entry (should evict key3)
        cache.put("key4", "value4")

        # key3 should be evicted
        assert cache.get("key3") is None
        assert cache.get("key1") == "value1"
        assert cache.get("key2") == "value2"
        assert cache.get("key4") == "value4"

    def test_cache_invalidate(self):
        """Test cache invalidation."""
        cache = Cache()

        cache.put("key1", "value1")
        cache.put("key2", "value2")

        # Invalidate one key
        removed = cache.invalidate("key1")
        assert removed is True
        assert cache.get("key1") is None
        assert cache.get("key2") == "value2"

        # Try to invalidate non-existent key
        removed = cache.invalidate("non_existent")
        assert removed is False

    def test_cache_invalidate_pattern(self):
        """Test pattern-based cache invalidation."""
        cache = Cache()

        # Put values with pattern
        cache.put("model:res.partner:1", "partner1")
        cache.put("model:res.partner:2", "partner2")
        cache.put("model:res.users:1", "user1")
        cache.put("other:key", "other_value")

        # Invalidate all partner entries
        count = cache.invalidate_pattern("model:res.partner:*")
        assert count == 2
        assert cache.get("model:res.partner:1") is None
        assert cache.get("model:res.partner:2") is None
        assert cache.get("model:res.users:1") == "user1"
        assert cache.get("other:key") == "other_value"

    def test_cache_clear(self):
        """Test clearing the cache."""
        cache = Cache()

        cache.put("key1", "value1")
        cache.put("key2", "value2")

        # Clear cache
        cache.clear()

        assert cache.get("key1") is None
        assert cache.get("key2") is None

        stats = cache.get_stats()
        assert stats["total_entries"] == 0
        # Note: misses are counted from the get() calls above
        assert stats["misses"] == 2


class TestRequestOptimizer:
    """Test RequestOptimizer functionality."""

    def test_field_usage_tracking(self):
        """Test tracking field usage."""
        optimizer = RequestOptimizer()

        # Track field usage
        optimizer.track_field_usage("res.partner", ["name", "email"])
        optimizer.track_field_usage("res.partner", ["name", "phone"])
        optimizer.track_field_usage("res.partner", ["name", "is_company"])

        # Get optimized fields
        fields = optimizer.get_optimized_fields("res.partner", None)

        # "name" should be first (used 3 times)
        assert fields[0] == "name"
        assert len(fields) <= 20

    def test_get_optimized_fields_with_requested(self):
        """Test optimized fields when specific fields are requested."""
        optimizer = RequestOptimizer()

        # Track some usage
        optimizer.track_field_usage("res.partner", ["name", "email"])

        # But request specific fields
        fields = optimizer.get_optimized_fields("res.partner", ["id", "display_name"])
        assert fields == ["id", "display_name"]

    def test_should_batch_request(self):
        """Test batch request logic."""
        optimizer = RequestOptimizer()

        # Large read should be batched
        assert optimizer.should_batch_request("res.partner", "read", 100) is True

        # Small read should not
        assert optimizer.should_batch_request("res.partner", "read", 10) is False

    def test_batch_queue(self):
        """Test batch queue operations."""
        optimizer = RequestOptimizer()

        # Add to batch
        optimizer.add_to_batch("res.partner", "read", {"ids": [1, 2, 3]})
        optimizer.add_to_batch("res.partner", "read", {"ids": [4, 5, 6]})

        # Get batch
        batch = optimizer.get_batch("res.partner", "read")
        assert len(batch) == 2
        assert batch[0]["ids"] == [1, 2, 3]
        assert batch[1]["ids"] == [4, 5, 6]

        # Queue should be empty now
        batch = optimizer.get_batch("res.partner", "read")
        assert len(batch) == 0
