"""Performance optimization and caching for Odoo MCP Server.

This module provides performance optimizations including:
- Connection pooling and reuse
- Intelligent response caching
- Request batching and optimization
- Performance monitoring and metrics
"""

import json
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple
from xmlrpc.client import ServerProxy

from .config import OdooConfig
from .logging_config import get_logger
from .performance_cache import Cache, CacheEntry, CacheStats, RequestOptimizer  # noqa: F401
from .xmlrpc_transport import DEFAULT_XMLRPC_TIMEOUT, transport_for_url

logger = get_logger(__name__)


class ConnectionPool:
    """Thread-safe connection pool for XML-RPC connections."""

    def __init__(self, config: OdooConfig, max_connections: int = 10):
        """Initialize connection pool.

        Args:
            config: Odoo configuration
            max_connections: Maximum number of connections
        """
        self.config = config
        self.max_connections = max_connections
        self._connections: List[Tuple[ServerProxy, float]] = []
        self._endpoint_map: List[str] = []  # Track endpoints for each connection
        self._lock = threading.RLock()
        self._transport = transport_for_url(config.url, DEFAULT_XMLRPC_TIMEOUT)
        self._last_cleanup = time.time()
        self._stats = {
            "connections_created": 0,
            "connections_reused": 0,
            "connections_closed": 0,
            "active_connections": 0,
        }

    def get_connection(self, endpoint: str) -> ServerProxy:
        """Get a connection from the pool.

        Args:
            endpoint: The endpoint path (e.g., '/xmlrpc/2/common')

        Returns:
            ServerProxy connection
        """
        with self._lock:
            now = time.time()

            # Cleanup stale connections periodically
            if now - self._last_cleanup > 60:  # Every minute
                self._cleanup_stale_connections()
                self._last_cleanup = now

            # Try to find an existing connection
            url = f"{self.config.url}{endpoint}"
            for i, (conn, last_used) in enumerate(self._connections):
                # Store endpoint with connection for matching
                if i < len(self._endpoint_map) and self._endpoint_map[i] == endpoint:
                    # Connection is still fresh (used within last 5 minutes)
                    if now - last_used < 300:
                        self._connections[i] = (conn, now)
                        self._stats["connections_reused"] += 1
                        logger.debug(f"Reusing connection for {endpoint}")
                        return conn
                    else:
                        # Connection is stale, remove it
                        self._connections.pop(i)
                        self._endpoint_map.pop(i)
                        self._stats["connections_closed"] += 1
                        break

            # Create new connection
            if len(self._connections) >= self.max_connections:
                # Remove oldest connection
                self._connections.pop(0)
                self._endpoint_map.pop(0)
                self._stats["connections_closed"] += 1

            conn = ServerProxy(url, transport=self._transport, allow_none=True)
            self._connections.append((conn, now))
            self._endpoint_map.append(endpoint)
            self._stats["connections_created"] += 1
            self._stats["active_connections"] = len(self._connections)
            logger.debug(f"Created new connection for {endpoint}")
            return conn

    def _cleanup_stale_connections(self):
        """Remove stale connections from pool."""
        now = time.time()
        initial_count = len(self._connections)

        # Remove connections older than 5 minutes
        new_connections = []
        new_endpoints = []
        for i, (conn, last_used) in enumerate(self._connections):
            if now - last_used < 300:
                new_connections.append((conn, last_used))
                new_endpoints.append(self._endpoint_map[i])

        self._connections = new_connections
        self._endpoint_map = new_endpoints

        removed = initial_count - len(self._connections)
        if removed > 0:
            self._stats["connections_closed"] += removed
            self._stats["active_connections"] = len(self._connections)
            logger.debug(f"Cleaned up {removed} stale connections")

    def get_stats(self) -> Dict[str, Any]:
        """Get connection pool statistics."""
        with self._lock:
            return self._stats.copy()

    def clear(self):
        """Clear all connections."""
        with self._lock:
            self._stats["connections_closed"] += len(self._connections)
            self._connections.clear()
            self._endpoint_map.clear()
            self._stats["active_connections"] = 0


class PerformanceMonitor:
    """Monitors and tracks performance metrics."""

    def __init__(self):
        """Initialize performance monitor."""
        self._metrics: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.RLock()
        self._start_time = time.time()

    @contextmanager
    def track_operation(self, operation: str):
        """Context manager to track operation duration.

        Args:
            operation: Operation name
        """
        start = time.time()
        try:
            yield
        finally:
            duration = time.time() - start
            with self._lock:
                self._metrics[operation].append(duration)
                # Keep only last 1000 measurements
                if len(self._metrics[operation]) > 1000:
                    self._metrics[operation] = self._metrics[operation][-1000:]

    def get_stats(self) -> Dict[str, Any]:
        """Get performance statistics."""
        with self._lock:
            stats: Dict[str, Any] = {
                "uptime_seconds": int(time.time() - self._start_time),
                "operations": {},
            }

            for operation, durations in self._metrics.items():
                if durations:
                    stats["operations"][operation] = {
                        "count": len(durations),
                        "avg_ms": round(sum(durations) / len(durations) * 1000, 2),
                        "min_ms": round(min(durations) * 1000, 2),
                        "max_ms": round(max(durations) * 1000, 2),
                        "last_ms": round(durations[-1] * 1000, 2),
                    }

            return stats


class PerformanceManager:
    """Central manager for all performance optimizations."""

    def __init__(self, config: OdooConfig):
        """Initialize performance manager.

        Args:
            config: Odoo configuration
        """
        self.config = config

        # Initialize components
        self.field_cache = Cache(max_size=100, max_memory_mb=10)
        self.record_cache = Cache(max_size=1000, max_memory_mb=50)
        self.permission_cache = Cache(max_size=500, max_memory_mb=5)
        self.connection_pool = ConnectionPool(config)
        self.request_optimizer = RequestOptimizer()
        self.monitor = PerformanceMonitor()

        logger.info("Performance manager initialized")

    def cache_key(self, prefix: str, **kwargs) -> str:
        """Generate cache key from parameters.

        Args:
            prefix: Key prefix
            **kwargs: Parameters to include in key

        Returns:
            Cache key string
        """
        # Sort kwargs for consistent keys
        sorted_items = sorted(kwargs.items())
        key_parts = [prefix]
        for k, v in sorted_items:
            if isinstance(v, (list, dict)):
                v = json.dumps(v, sort_keys=True)
            key_parts.append(f"{k}:{v}")
        return ":".join(key_parts)

    def get_cached_fields(self, model: str) -> Optional[Dict[str, Any]]:
        """Get cached field definitions.

        Args:
            model: Model name

        Returns:
            Cached fields or None
        """
        key = self.cache_key("fields", model=model)
        return self.field_cache.get(key)

    def cache_fields(self, model: str, fields: Dict[str, Any]):
        """Cache field definitions.

        Args:
            model: Model name
            fields: Field definitions
        """
        key = self.cache_key("fields", model=model)
        # Fields rarely change, cache for 1 hour
        self.field_cache.put(key, fields, ttl_seconds=3600)

    def get_cached_record(
        self, model: str, record_id: int, fields: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """Get cached record.

        Args:
            model: Model name
            record_id: Record ID
            fields: Field list (for cache key)

        Returns:
            Cached record or None
        """
        key = self.cache_key("record", model=model, id=record_id, fields=fields)
        return self.record_cache.get(key)

    def cache_record(
        self,
        model: str,
        record: Dict[str, Any],
        fields: Optional[List[str]] = None,
        ttl_seconds: int = 300,
    ):
        """Cache record data.

        Args:
            model: Model name
            record: Record data
            fields: Field list (for cache key)
            ttl_seconds: Cache TTL
        """
        record_id = record.get("id")
        if record_id is not None:
            key = self.cache_key("record", model=model, id=record_id, fields=fields)
            self.record_cache.put(key, record, ttl_seconds=ttl_seconds)

    def invalidate_record_cache(self, model: str, record_id: Optional[int] = None):
        """Invalidate record cache.

        Args:
            model: Model name
            record_id: Specific record ID or None for all model records
        """
        if record_id:
            # Use wildcard pattern that will match any fields value
            pattern = f"record:*id:{record_id}:model:{model}*"
        else:
            pattern = f"record:*model:{model}*"

        count = self.record_cache.invalidate_pattern(pattern)
        if count > 0:
            logger.debug(f"Invalidated {count} cache entries for {pattern}")

    def get_cached_permission(self, model: str, operation: str, user_id: int) -> Optional[bool]:
        """Get cached permission check.

        Args:
            model: Model name
            operation: Operation type
            user_id: User ID

        Returns:
            Cached permission or None
        """
        key = self.cache_key("permission", model=model, operation=operation, user_id=user_id)
        return self.permission_cache.get(key)

    def cache_permission(self, model: str, operation: str, user_id: int, allowed: bool):
        """Cache permission check result.

        Args:
            model: Model name
            operation: Operation type
            user_id: User ID
            allowed: Permission result
        """
        key = self.cache_key("permission", model=model, operation=operation, user_id=user_id)
        # Permissions may change, cache for 5 minutes
        self.permission_cache.put(key, allowed, ttl_seconds=300)

    def get_optimized_connection(self, endpoint: str) -> Any:
        """Get optimized connection from pool.

        Args:
            endpoint: Endpoint path

        Returns:
            Connection object
        """
        with self.monitor.track_operation("connection_get"):
            return self.connection_pool.get_connection(endpoint)

    def optimize_search_fields(
        self, model: str, requested_fields: Optional[List[str]] = None
    ) -> List[str]:
        """Optimize field selection for search.

        Args:
            model: Model name
            requested_fields: Explicitly requested fields

        Returns:
            Optimized field list
        """
        optimized = self.request_optimizer.get_optimized_fields(model, requested_fields)
        self.request_optimizer.track_field_usage(model, optimized)
        return optimized

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive performance statistics."""
        return {
            "caches": {
                "field_cache": self.field_cache.get_stats(),
                "record_cache": self.record_cache.get_stats(),
                "permission_cache": self.permission_cache.get_stats(),
            },
            "connection_pool": self.connection_pool.get_stats(),
            "performance": self.monitor.get_stats(),
        }

    def clear_all_caches(self):
        """Clear all caches."""
        self.field_cache.clear()
        self.record_cache.clear()
        self.permission_cache.clear()
        logger.info("All caches cleared")
