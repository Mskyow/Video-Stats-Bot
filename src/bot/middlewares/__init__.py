"""Bot middlewares package."""

from .album import AlbumMiddleware
from .auth import AuthMiddleware

__all__ = ["AlbumMiddleware", "AuthMiddleware"]
