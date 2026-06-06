# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Callable, Optional, TYPE_CHECKING, Union

from flask import current_app, Flask
from flask_caching import Cache
from markupsafe import Markup

from superset.utils.core import DatasourceType

if TYPE_CHECKING:
    from superset.async_events.cache_backend import (
        RedisCacheBackend,
        RedisSentinelCacheBackend,
    )

logger = logging.getLogger(__name__)

# Redis-based CACHE_TYPE values that benefit from failover resilience settings
_REDIS_CACHE_TYPES = frozenset(
    {"RedisCache", "RedisSentinelCache", "RedisClusterCache"}
)

# Default health check interval (seconds) for redis-py connection pool validation.
# redis-py will send a PING before reusing a connection that has been idle longer
# than this interval, detecting dead connections from failover events.
_DEFAULT_HEALTH_CHECK_INTERVAL = 10

# Default socket timeout (seconds) for Redis operations.
_DEFAULT_SOCKET_TIMEOUT = 5

# Default socket connect timeout (seconds).
_DEFAULT_SOCKET_CONNECT_TIMEOUT = 3

CACHE_IMPORT_PATH = "superset.extensions.metastore_cache.SupersetMetastoreCache"

# Hash function lookup table matching superset.utils.hashing
_HASH_METHODS: dict[str, Callable[..., Any]] = {
    "sha256": hashlib.sha256,
    "md5": hashlib.md5,
}


class ConfigurableHashMethod:
    """
    A callable that defers hash algorithm selection to runtime.

    Flask-caching's memoize decorator evaluates hash_method at decoration time
    (module import), but we need to read HASH_ALGORITHM config at function call
    time when the app context is available.

    This class acts like a hashlib function but looks up the configured
    algorithm when called.
    """

    def __call__(self, data: bytes = b"") -> Any:
        """
        Create a hash object using the configured algorithm.

        Args:
            data: Optional initial data to hash

        Returns:
            A hashlib hash object (e.g., sha256 or md5)

        Raises:
            ValueError: If HASH_ALGORITHM is set to an unsupported value
        """
        algorithm = current_app.config["HASH_ALGORITHM"]
        hash_func = _HASH_METHODS.get(algorithm)
        if hash_func is None:
            raise ValueError(f"Unsupported hash algorithm: {algorithm}")
        return hash_func(data)


# Singleton instance to use as default hash_method
configurable_hash_method = ConfigurableHashMethod()


class SupersetCache(Cache):
    """
    Cache subclass that uses the configured HASH_ALGORITHM instead of MD5.

    Flask-caching uses MD5 by default for cache key generation, which fails
    in FIPS mode where MD5 is disabled. This class overrides the default
    hash method to use the algorithm specified by HASH_ALGORITHM config.

    Note: Switching hash algorithms will invalidate existing cache keys,
    causing a one-time cache miss on upgrade.
    """

    def memoize(
        self,
        timeout: int | None = None,
        make_name: Callable[..., Any] | None = None,
        unless: Callable[..., bool] | None = None,
        forced_update: Callable[..., bool] | None = None,
        response_filter: Callable[..., Any] | None = None,
        hash_method: Callable[..., Any] = configurable_hash_method,
        cache_none: bool = False,
        source_check: bool | None = None,
        args_to_ignore: Any | None = None,
    ) -> Callable[..., Any]:
        return super().memoize(
            timeout=timeout,
            make_name=make_name,
            unless=unless,
            forced_update=forced_update,
            response_filter=response_filter,
            hash_method=hash_method,
            cache_none=cache_none,
            source_check=source_check,
            args_to_ignore=args_to_ignore,
        )

    def cached(
        self,
        timeout: int | None = None,
        key_prefix: str = "view/%s",
        unless: Callable[..., bool] | None = None,
        forced_update: Callable[..., bool] | None = None,
        response_filter: Callable[..., Any] | None = None,
        query_string: bool = False,
        hash_method: Callable[..., Any] = configurable_hash_method,
        cache_none: bool = False,
        make_cache_key: Callable[..., Any] | None = None,
        source_check: bool | None = None,
        response_hit_indication: bool | None = False,
    ) -> Callable[..., Any]:
        return super().cached(
            timeout=timeout,
            key_prefix=key_prefix,
            unless=unless,
            forced_update=forced_update,
            response_filter=response_filter,
            query_string=query_string,
            hash_method=hash_method,
            cache_none=cache_none,
            make_cache_key=make_cache_key,
            source_check=source_check,
            response_hit_indication=response_hit_indication,
        )

    # pylint: disable=protected-access
    def _memoize_make_cache_key(
        self,
        make_name: Callable[..., Any] | None = None,
        timeout: Callable[..., Any] | None = None,
        forced_update: bool = False,
        hash_method: Callable[..., Any] = configurable_hash_method,
        source_check: bool | None = False,
        args_to_ignore: Any | None = None,
    ) -> Callable[..., Any]:
        return super()._memoize_make_cache_key(
            make_name=make_name,
            timeout=timeout,
            forced_update=forced_update,
            hash_method=hash_method,
            source_check=source_check,
            args_to_ignore=args_to_ignore,
        )


class ExploreFormDataCache(SupersetCache):
    def get(self, *args: Any, **kwargs: Any) -> Optional[Union[str, Markup]]:
        cache = self.cache.get(*args, **kwargs)

        if not cache:
            return None

        # rename data keys for existing cache based on new TemporaryExploreState model
        if isinstance(cache, dict):
            cache = {
                ("datasource_id" if key == "dataset_id" else key): value
                for (key, value) in cache.items()
            }
            # add default datasource_type if it doesn't exist
            # temporarily defaulting to table until sqlatables are deprecated
            if "datasource_type" not in cache:
                cache["datasource_type"] = DatasourceType.TABLE

        return cache


class CacheManager:
    def __init__(self) -> None:
        super().__init__()

        self._cache = SupersetCache()
        self._data_cache = SupersetCache()
        self._thumbnail_cache = SupersetCache()
        self._filter_state_cache = SupersetCache()
        self._explore_form_data_cache = ExploreFormDataCache()
        self._distributed_coordination: (
            RedisCacheBackend | RedisSentinelCacheBackend | None
        ) = None

    @staticmethod
    def _apply_redis_failover_defaults(cache_config: dict[str, Any]) -> None:
        """Inject failover-resilient defaults into CACHE_OPTIONS for Redis backends.

        When a Redis Cluster or Sentinel failover occurs, connections in the pool
        may point to a demoted replica. redis-py's ``health_check_interval``
        causes a PING before reusing idle connections, quickly detecting dead
        sockets. ``socket_timeout`` and ``socket_connect_timeout`` bound how long
        the client waits on unresponsive nodes. ``retry_on_error`` enables
        automatic retry for transient connection errors during failover.

        These defaults are only applied when not already set by the operator.
        """
        options: dict[str, Any] = cache_config.setdefault("CACHE_OPTIONS", {})

        options.setdefault("health_check_interval", _DEFAULT_HEALTH_CHECK_INTERVAL)
        options.setdefault("socket_timeout", _DEFAULT_SOCKET_TIMEOUT)
        options.setdefault("socket_connect_timeout", _DEFAULT_SOCKET_CONNECT_TIMEOUT)

        if "retry_on_error" not in options:
            try:
                from redis.exceptions import (
                    BusyLoadingError,
                    ConnectionError as RedisConnectionError,
                    TimeoutError as RedisTimeoutError,
                )

                options["retry_on_error"] = [
                    RedisConnectionError,
                    RedisTimeoutError,
                    BusyLoadingError,
                ]
            except ImportError:
                pass

        if "retry" not in options:
            try:
                from redis.backoff import ExponentialBackoff
                from redis.retry import Retry

                options["retry"] = Retry(
                    ExponentialBackoff(cap=0.5, base=0.1), retries=3
                )
            except ImportError:
                pass

    @staticmethod
    def _init_cache(
        app: Flask, cache: Cache, cache_config_key: str, required: bool = False
    ) -> None:
        cache_config = app.config[cache_config_key]
        cache_type = cache_config.get("CACHE_TYPE")
        if (required and cache_type is None) or cache_type == "SupersetMetastoreCache":
            if cache_type is None and not app.debug:
                logger.warning(
                    "Falling back to the built-in cache, that stores data in the "
                    "metadata database, for the following cache: `%s`. "
                    "It is recommended to use `RedisCache`, `MemcachedCache` or "
                    "another dedicated caching backend for production deployments",
                    cache_config_key,
                )
            cache_type = CACHE_IMPORT_PATH
            cache_key_prefix = cache_config.get("CACHE_KEY_PREFIX", cache_config_key)
            cache_config.update(
                {"CACHE_TYPE": cache_type, "CACHE_KEY_PREFIX": cache_key_prefix}
            )

        if cache_type is not None and "CACHE_DEFAULT_TIMEOUT" not in cache_config:
            default_timeout = app.config.get("CACHE_DEFAULT_TIMEOUT")
            cache_config["CACHE_DEFAULT_TIMEOUT"] = default_timeout

        # Inject failover-resilient defaults for Redis-based backends
        if cache_type in _REDIS_CACHE_TYPES:
            CacheManager._apply_redis_failover_defaults(cache_config)
            logger.info(
                "Redis failover resilience enabled for %s "
                "(health_check_interval=%ds, socket_timeout=%ds)",
                cache_config_key,
                cache_config.get("CACHE_OPTIONS", {}).get(
                    "health_check_interval", _DEFAULT_HEALTH_CHECK_INTERVAL
                ),
                cache_config.get("CACHE_OPTIONS", {}).get(
                    "socket_timeout", _DEFAULT_SOCKET_TIMEOUT
                ),
            )

        cache.init_app(app, cache_config)

    def init_app(self, app: Flask) -> None:
        self._init_cache(app, self._cache, "CACHE_CONFIG")
        self._init_cache(app, self._data_cache, "DATA_CACHE_CONFIG")
        self._init_cache(app, self._thumbnail_cache, "THUMBNAIL_CACHE_CONFIG")
        self._init_cache(
            app, self._filter_state_cache, "FILTER_STATE_CACHE_CONFIG", required=True
        )
        self._init_cache(
            app,
            self._explore_form_data_cache,
            "EXPLORE_FORM_DATA_CACHE_CONFIG",
            required=True,
        )
        self._init_distributed_coordination(app)

    def _init_distributed_coordination(self, app: Flask) -> None:
        """Initialize the distributed coordination backend (pub/sub, locks, streams)."""
        from superset.async_events.cache_backend import (
            RedisCacheBackend,
            RedisSentinelCacheBackend,
        )

        config = app.config.get("DISTRIBUTED_COORDINATION_CONFIG")
        if not config:
            return

        cache_type = config.get("CACHE_TYPE")
        if cache_type == "RedisCache":
            self._distributed_coordination = RedisCacheBackend.from_config(config)
        elif cache_type == "RedisSentinelCache":
            self._distributed_coordination = RedisSentinelCacheBackend.from_config(
                config
            )
        else:
            logger.warning(
                "Unsupported CACHE_TYPE for DISTRIBUTED_COORDINATION_CONFIG: %s. "
                "Use 'RedisCache' or 'RedisSentinelCache'.",
                cache_type,
            )

    @property
    def data_cache(self) -> Cache:
        return self._data_cache

    @property
    def cache(self) -> Cache:
        return self._cache

    @property
    def thumbnail_cache(self) -> Cache:
        return self._thumbnail_cache

    @property
    def filter_state_cache(self) -> Cache:
        return self._filter_state_cache

    @property
    def explore_form_data_cache(self) -> Cache:
        return self._explore_form_data_cache

    @property
    def distributed_coordination(
        self,
    ) -> RedisCacheBackend | RedisSentinelCacheBackend | None:
        """
        Return the distributed coordination backend for Redis-specific primitives.

        This backend is the foundation for distributed coordination features including
        pub/sub messaging, atomic distributed locking, and streams. A higher-level
        service will eventually expose standardized interfaces on top of this backend.

        Coordination primitives currently backed by this:
        - Pub/Sub messaging for real-time abort/completion notifications
        - SET NX EX for atomic distributed lock acquisition

        The backend provides:
        - `._cache`: Raw Redis client
        - `.key_prefix`: Configured key prefix (from CACHE_KEY_PREFIX)
        - `.default_timeout`: Default timeout in seconds (from CACHE_DEFAULT_TIMEOUT)

        Returns None if DISTRIBUTED_COORDINATION_CONFIG is not configured.
        """
        return self._distributed_coordination

    def check_cache_health(self) -> dict[str, bool]:
        """Verify connectivity to all configured cache backends.

        Returns a dict mapping cache name to health status. Logs warnings for
        any backend that fails the health check, which may indicate a Redis
        failover event or network partition.
        """
        results: dict[str, bool] = {}
        caches = {
            "cache": self._cache,
            "data_cache": self._data_cache,
            "thumbnail_cache": self._thumbnail_cache,
            "filter_state_cache": self._filter_state_cache,
            "explore_form_data_cache": self._explore_form_data_cache,
        }

        for name, cache_instance in caches.items():
            try:
                backend = cache_instance.cache
                # NullCache and metastore backends don't have a Redis client
                redis_client = getattr(backend, "_write_client", None)
                if redis_client is not None and hasattr(redis_client, "ping"):
                    start = time.monotonic()
                    redis_client.ping()
                    latency_ms = (time.monotonic() - start) * 1000
                    results[name] = True
                    if latency_ms > 100:
                        logger.warning(
                            "Cache health check slow for %s: %.1fms "
                            "(possible failover or network issue)",
                            name,
                            latency_ms,
                        )
                else:
                    # Non-Redis backend, assume healthy
                    results[name] = True
            except Exception:
                results[name] = False
                logger.warning(
                    "Cache health check FAILED for %s — potential Redis failover "
                    "or connectivity issue. Stale reads may occur until "
                    "connection pool recovers.",
                    name,
                    exc_info=True,
                )

        return results
