"""Storage backends. Default is Redis; swap by implementing ``StorageBackend``."""

from waiting_room.core.storage.redis_backend import RedisStorageBackend

__all__ = ["RedisStorageBackend"]
