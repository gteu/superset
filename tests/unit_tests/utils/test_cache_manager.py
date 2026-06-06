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
import hashlib
from unittest.mock import MagicMock, patch

import pytest
from redis.exceptions import (
    BusyLoadingError,
    ConnectionError as RedisConnectionError,
    TimeoutError as RedisTimeoutError,
)

from superset.utils.cache_manager import (
    _DEFAULT_HEALTH_CHECK_INTERVAL,
    _DEFAULT_SOCKET_CONNECT_TIMEOUT,
    _DEFAULT_SOCKET_TIMEOUT,
    _REDIS_CACHE_TYPES,
    CacheManager,
    configurable_hash_method,
    ConfigurableHashMethod,
    SupersetCache,
)


def test_configurable_hash_method_uses_sha256():
    """Test ConfigurableHashMethod uses sha256 when configured."""
    mock_app = MagicMock()
    mock_app.config = {"HASH_ALGORITHM": "sha256"}

    with patch("superset.utils.cache_manager.current_app", mock_app):
        hash_obj = configurable_hash_method(b"test")
        # Verify it returns a sha256 hash object
        assert hash_obj.hexdigest() == hashlib.sha256(b"test").hexdigest()


def test_configurable_hash_method_uses_md5():
    """Test ConfigurableHashMethod uses md5 when configured."""
    mock_app = MagicMock()
    mock_app.config = {"HASH_ALGORITHM": "md5"}

    with patch("superset.utils.cache_manager.current_app", mock_app):
        hash_obj = configurable_hash_method(b"test")
        # Verify it returns a md5 hash object
        assert hash_obj.hexdigest() == hashlib.md5(b"test").hexdigest()  # noqa: S324


def test_configurable_hash_method_empty_data():
    """Test ConfigurableHashMethod with empty data."""
    mock_app = MagicMock()
    mock_app.config = {"HASH_ALGORITHM": "sha256"}

    with patch("superset.utils.cache_manager.current_app", mock_app):
        hash_obj = configurable_hash_method()
        assert hash_obj.hexdigest() == hashlib.sha256(b"").hexdigest()


def test_configurable_hash_method_is_callable():
    """Test that ConfigurableHashMethod instance is callable."""
    method = ConfigurableHashMethod()
    assert callable(method)


def test_superset_cache_memoize_uses_configurable_hash():
    """Test that SupersetCache.memoize uses configurable_hash_method by default."""
    cache = SupersetCache()

    with patch.object(
        cache.__class__.__bases__[0], "memoize", return_value=lambda f: f
    ) as mock_memoize:
        cache.memoize(timeout=300)

        mock_memoize.assert_called_once()
        call_kwargs = mock_memoize.call_args[1]
        assert call_kwargs["hash_method"] is configurable_hash_method


def test_superset_cache_memoize_allows_explicit_hash_method():
    """Test that SupersetCache.memoize allows explicit hash_method override."""
    cache = SupersetCache()

    with patch.object(
        cache.__class__.__bases__[0], "memoize", return_value=lambda f: f
    ) as mock_memoize:
        cache.memoize(timeout=300, hash_method=hashlib.md5)

        mock_memoize.assert_called_once()
        call_kwargs = mock_memoize.call_args[1]
        assert call_kwargs["hash_method"] == hashlib.md5


def test_superset_cache_cached_uses_configurable_hash():
    """Test that SupersetCache.cached uses configurable_hash_method by default."""
    cache = SupersetCache()

    with patch.object(
        cache.__class__.__bases__[0], "cached", return_value=lambda f: f
    ) as mock_cached:
        cache.cached(timeout=300)

        mock_cached.assert_called_once()
        call_kwargs = mock_cached.call_args[1]
        assert call_kwargs["hash_method"] is configurable_hash_method


def test_superset_cache_cached_allows_explicit_hash_method():
    """Test that SupersetCache.cached allows explicit hash_method override."""
    cache = SupersetCache()

    with patch.object(
        cache.__class__.__bases__[0], "cached", return_value=lambda f: f
    ) as mock_cached:
        cache.cached(timeout=300, hash_method=hashlib.md5)

        mock_cached.assert_called_once()
        call_kwargs = mock_cached.call_args[1]
        assert call_kwargs["hash_method"] == hashlib.md5


def test_superset_cache_memoize_make_cache_key_uses_configurable_hash():
    """Test _memoize_make_cache_key uses configurable_hash_method by default."""
    cache = SupersetCache()

    with patch.object(
        cache.__class__.__bases__[0],
        "_memoize_make_cache_key",
        return_value=lambda *args, **kwargs: "cache_key",
    ) as mock_make_key:
        cache._memoize_make_cache_key(make_name=None, timeout=300)

        mock_make_key.assert_called_once()
        call_kwargs = mock_make_key.call_args[1]
        assert call_kwargs["hash_method"] is configurable_hash_method


def test_superset_cache_memoize_make_cache_key_allows_explicit_hash():
    """Test _memoize_make_cache_key allows explicit hash_method override."""
    cache = SupersetCache()

    with patch.object(
        cache.__class__.__bases__[0],
        "_memoize_make_cache_key",
        return_value=lambda *args, **kwargs: "cache_key",
    ) as mock_make_key:
        cache._memoize_make_cache_key(
            make_name=None, timeout=300, hash_method=hashlib.md5
        )

        mock_make_key.assert_called_once()
        call_kwargs = mock_make_key.call_args[1]
        assert call_kwargs["hash_method"] == hashlib.md5


@pytest.mark.parametrize(
    "algorithm,expected_digest",
    [
        ("sha256", hashlib.sha256(b"test_data").hexdigest()),
        ("md5", hashlib.md5(b"test_data").hexdigest()),  # noqa: S324
    ],
)
def test_configurable_hash_method_parametrized(algorithm, expected_digest):
    """Parametrized test for ConfigurableHashMethod with different algorithms."""
    mock_app = MagicMock()
    mock_app.config = {"HASH_ALGORITHM": algorithm}

    with patch("superset.utils.cache_manager.current_app", mock_app):
        hash_obj = configurable_hash_method(b"test_data")
        assert hash_obj.hexdigest() == expected_digest


@pytest.mark.parametrize("cache_type", list(_REDIS_CACHE_TYPES))
def test_apply_redis_failover_defaults_injects_options(cache_type):
    """Test that Redis failover defaults are injected for Redis-based backends."""
    cache_config: dict = {"CACHE_TYPE": cache_type}
    CacheManager._apply_redis_failover_defaults(cache_config)

    options = cache_config["CACHE_OPTIONS"]
    assert options["health_check_interval"] == _DEFAULT_HEALTH_CHECK_INTERVAL
    assert options["socket_timeout"] == _DEFAULT_SOCKET_TIMEOUT
    assert options["socket_connect_timeout"] == _DEFAULT_SOCKET_CONNECT_TIMEOUT
    assert RedisConnectionError in options["retry_on_error"]
    assert RedisTimeoutError in options["retry_on_error"]
    assert BusyLoadingError in options["retry_on_error"]
    assert options["retry"] is not None


def test_apply_redis_failover_defaults_respects_existing_options():
    """Test that operator-provided CACHE_OPTIONS are not overwritten."""
    cache_config: dict = {
        "CACHE_TYPE": "RedisCache",
        "CACHE_OPTIONS": {
            "health_check_interval": 30,
            "socket_timeout": 15,
        },
    }
    CacheManager._apply_redis_failover_defaults(cache_config)

    options = cache_config["CACHE_OPTIONS"]
    # Operator values preserved
    assert options["health_check_interval"] == 30
    assert options["socket_timeout"] == 15
    # Missing values filled in with defaults
    assert options["socket_connect_timeout"] == _DEFAULT_SOCKET_CONNECT_TIMEOUT


def test_apply_redis_failover_defaults_no_options_key():
    """Test CACHE_OPTIONS dict is created when missing."""
    cache_config: dict = {"CACHE_TYPE": "RedisClusterCache"}
    CacheManager._apply_redis_failover_defaults(cache_config)

    assert "CACHE_OPTIONS" in cache_config
    assert cache_config["CACHE_OPTIONS"]["health_check_interval"] == (
        _DEFAULT_HEALTH_CHECK_INTERVAL
    )


def test_init_cache_applies_failover_for_redis():
    """Test _init_cache injects failover settings for Redis backends."""
    mock_app = MagicMock()
    mock_app.config = {
        "TEST_CACHE": {
            "CACHE_TYPE": "RedisCache",
            "CACHE_REDIS_URL": "redis://localhost:6379/0",
        },
        "CACHE_DEFAULT_TIMEOUT": 300,
    }
    mock_app.debug = False

    cache = SupersetCache()
    with patch.object(cache, "init_app"):
        CacheManager._init_cache(mock_app, cache, "TEST_CACHE")

    options = mock_app.config["TEST_CACHE"]["CACHE_OPTIONS"]
    assert options["health_check_interval"] == _DEFAULT_HEALTH_CHECK_INTERVAL


def test_init_cache_skips_failover_for_non_redis():
    """Test _init_cache does NOT inject failover settings for NullCache."""
    mock_app = MagicMock()
    mock_app.config = {
        "TEST_CACHE": {"CACHE_TYPE": "NullCache"},
        "CACHE_DEFAULT_TIMEOUT": 300,
    }
    mock_app.debug = False

    cache = SupersetCache()
    with patch.object(cache, "init_app"):
        CacheManager._init_cache(mock_app, cache, "TEST_CACHE")

    assert "CACHE_OPTIONS" not in mock_app.config["TEST_CACHE"]


def test_check_cache_health_healthy():
    """Test check_cache_health returns True for healthy Redis backends."""
    manager = CacheManager()

    mock_redis = MagicMock()
    mock_redis.ping.return_value = True

    mock_backend = MagicMock()
    mock_backend._write_client = mock_redis

    manager._cache = MagicMock()
    manager._cache.cache = mock_backend
    manager._data_cache = MagicMock()
    manager._data_cache.cache = mock_backend
    manager._thumbnail_cache = MagicMock()
    manager._thumbnail_cache.cache = mock_backend
    manager._filter_state_cache = MagicMock()
    manager._filter_state_cache.cache = mock_backend
    manager._explore_form_data_cache = MagicMock()
    manager._explore_form_data_cache.cache = mock_backend

    results = manager.check_cache_health()
    assert all(results.values())
    assert len(results) == 5


def test_check_cache_health_unhealthy():
    """Test check_cache_health returns False when Redis ping raises."""
    manager = CacheManager()

    mock_redis = MagicMock()
    mock_redis.ping.side_effect = RedisConnectionError("Connection refused")

    mock_backend = MagicMock()
    mock_backend._write_client = mock_redis

    manager._cache = MagicMock()
    manager._cache.cache = mock_backend
    manager._data_cache = MagicMock()
    manager._data_cache.cache = MagicMock()
    manager._data_cache.cache._write_client = mock_redis
    manager._thumbnail_cache = MagicMock()
    manager._thumbnail_cache.cache = MagicMock(spec=[])
    manager._filter_state_cache = MagicMock()
    manager._filter_state_cache.cache = MagicMock(spec=[])
    manager._explore_form_data_cache = MagicMock()
    manager._explore_form_data_cache.cache = MagicMock(spec=[])

    results = manager.check_cache_health()
    assert results["cache"] is False
    assert results["data_cache"] is False
    # Non-Redis backends (no _write_client) should be healthy
    assert results["thumbnail_cache"] is True
