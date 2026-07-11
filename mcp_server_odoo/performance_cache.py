# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
"""Caching and request optimization for Odoo MCP Server.

This module provides the cache primitives used by the performance layer:
- Intelligent response caching (LRU + TTL)
- Request batching and optimization
"""

import json
import threading
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class CacheEntry:
    """Represents a cached item with metadata."""

    key: str
    value: Any
    created_at: datetime
    accessed_at: datetime
    ttl_seconds: int
    hit_count: int = 0
    size_bytes: int = 0

    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        age = datetime.now() - self.created_at
        return age.total_seconds() > self.ttl_seconds

    def access(self):
        """Update access metadata."""
        self.accessed_at = datetime.now()
        self.hit_count += 1


@dataclass
class CacheStats:
    """Cache performance statistics."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expired_evictions: int = 0
    size_evictions: int = 0
    total_entries: int = 0
    total_size_bytes: int = 0

    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def record_hit(self):
        """Record a cache hit."""
        self.hits += 1

    def record_miss(self):
        """Record a cache miss."""
        self.misses += 1

    def record_eviction(self, reason: str = "manual"):
        """Record a cache eviction."""
        self.evictions += 1
        if reason == "expired":
            self.expired_evictions += 1
        elif reason == "size":
            self.size_evictions += 1


class Cache:
    """Thread-safe LRU cache with TTL support."""

    def __init__(self, max_size: int = 1000, max_memory_mb: int = 100):
        """Initialize cache.

        Args:
            max_size: Maximum number of entries
            max_memory_mb: Maximum memory usage in MB
        """
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._max_size = max_size
        self._max_memory_bytes = max_memory_mb * 1024 * 1024
        self._stats = CacheStats()

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._stats.record_miss()
                return None

            if entry.is_expired():
                self._remove(key, reason="expired")
                self._stats.record_miss()
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            entry.access()
            self._stats.record_hit()
            return entry.value

    def put(self, key: str, value: Any, ttl_seconds: int = 300):
        """Put value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl_seconds: Time to live in seconds
        """
        with self._lock:
            # Calculate size (rough estimate)
            size_bytes = len(json.dumps(value, default=str).encode())

            # Check memory limit
            if self._stats.total_size_bytes + size_bytes > self._max_memory_bytes:
                self._evict_lru(reason="size")

            # Check size limit
            while len(self._cache) >= self._max_size:
                self._evict_lru(reason="size")

            # Add or update entry
            now = datetime.now()
            if key in self._cache:
                old_size = self._cache[key].size_bytes
                self._stats.total_size_bytes -= old_size

            entry = CacheEntry(
                key=key,
                value=value,
                created_at=now,
                accessed_at=now,
                ttl_seconds=ttl_seconds,
                size_bytes=size_bytes,
            )

            self._cache[key] = entry
            self._cache.move_to_end(key)
            self._stats.total_entries = len(self._cache)
            self._stats.total_size_bytes += size_bytes

    def invalidate(self, key: str) -> bool:
        """Invalidate a cache entry.

        Args:
            key: Cache key

        Returns:
            True if entry was removed, False if not found
        """
        with self._lock:
            return self._remove(key, reason="manual")

    def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate all entries matching pattern.

        Args:
            pattern: Pattern to match (e.g., "model:res.partner:*")

        Returns:
            Number of entries invalidated
        """
        with self._lock:
            count = 0
            keys_to_remove = []

            # Enhanced pattern matching with * wildcard
            if "*" in pattern:
                # Handle patterns with wildcards
                parts = pattern.split("*")
                keys_to_remove = []
                for k in self._cache.keys():
                    # Check if all non-wildcard parts are in the key in order
                    key_matches = True
                    search_from = 0
                    for part in parts:
                        if part:  # Skip empty parts from consecutive wildcards
                            idx = k.find(part, search_from)
                            if idx == -1:
                                key_matches = False
                                break
                            search_from = idx + len(part)
                    if key_matches:
                        keys_to_remove.append(k)
            else:
                if pattern in self._cache:
                    keys_to_remove = [pattern]

            for key in keys_to_remove:
                if self._remove(key, reason="manual"):
                    count += 1

            return count

    def clear(self):
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
            self._stats = CacheStats()

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            return {
                "hits": self._stats.hits,
                "misses": self._stats.misses,
                "hit_rate": round(self._stats.hit_rate, 3),
                "evictions": self._stats.evictions,
                "expired_evictions": self._stats.expired_evictions,
                "size_evictions": self._stats.size_evictions,
                "total_entries": self._stats.total_entries,
                "total_size_mb": round(self._stats.total_size_bytes / (1024 * 1024), 2),
                "max_size": self._max_size,
                "max_memory_mb": self._max_memory_bytes / (1024 * 1024),
            }

    def _remove(self, key: str, reason: str = "manual") -> bool:
        """Remove entry from cache."""
        if key in self._cache:
            entry = self._cache.pop(key)
            self._stats.total_size_bytes -= entry.size_bytes
            self._stats.total_entries = len(self._cache)
            self._stats.record_eviction(reason)
            return True
        return False

    def _evict_lru(self, reason: str = "size"):
        """Evict least recently used entry."""
        if self._cache:
            # OrderedDict maintains order, first item is LRU
            key = next(iter(self._cache))
            self._remove(key, reason)


class RequestOptimizer:
    """Optimizes Odoo requests for better performance."""

    def __init__(self):
        """Initialize request optimizer."""
        self._batch_queue: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._field_usage: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._lock = threading.RLock()

    def track_field_usage(self, model: str, fields: List[str]):
        """Track which fields are commonly requested.

        Args:
            model: Model name
            fields: List of field names
        """
        with self._lock:
            for field in fields:
                self._field_usage[model][field] += 1

    def get_optimized_fields(self, model: str, requested_fields: Optional[List[str]]) -> List[str]:
        """Get optimized field list based on usage patterns.

        Args:
            model: Model name
            requested_fields: Explicitly requested fields

        Returns:
            Optimized field list
        """
        if requested_fields:
            return requested_fields

        with self._lock:
            usage = self._field_usage.get(model, {})
            if not usage:
                # Return common fields if no usage data
                return ["id", "name", "display_name"]

            # Get top 20 most used fields
            sorted_fields = sorted(usage.items(), key=lambda x: x[1], reverse=True)
            return [field for field, _ in sorted_fields[:20]]

    def should_batch_request(self, model: str, operation: str, size: int) -> bool:
        """Determine if request should be batched.

        Args:
            model: Model name
            operation: Operation type (read, search, etc.)
            size: Number of records

        Returns:
            True if request should be batched
        """
        # Batch if requesting many records
        if operation == "read" and size > 50:
            return True

        # Batch if multiple small requests for same model
        with self._lock:
            queue_size = len(self._batch_queue.get(f"{model}:{operation}", []))
            return queue_size > 0

    def add_to_batch(self, model: str, operation: str, params: Dict[str, Any]):
        """Add request to batch queue.

        Args:
            model: Model name
            operation: Operation type
            params: Request parameters
        """
        with self._lock:
            key = f"{model}:{operation}"
            self._batch_queue[key].append(params)

    def get_batch(self, model: str, operation: str) -> List[Dict[str, Any]]:
        """Get and clear batch for processing.

        Args:
            model: Model name
            operation: Operation type

        Returns:
            List of batched requests
        """
        with self._lock:
            key = f"{model}:{operation}"
            batch = self._batch_queue[key]
            self._batch_queue[key] = []
            return batch
