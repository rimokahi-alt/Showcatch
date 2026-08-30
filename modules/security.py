import re
import time
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimiter:
    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def is_limited(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window_seconds
        self._hits[key] = [t for t in self._hits[key] if t > window_start]
        if len(self._hits[key]) >= self.max_requests:
            return True
        self._hits[key].append(now)
        return False


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' https://m.media-amazon.com https://imdb.com data:; "
            "media-src 'self'; "
            "font-src 'self'; "
            "connect-src 'self';"
        )
        return response


general_limiter = RateLimiter(max_requests=30, window_seconds=60)
auth_limiter = RateLimiter(max_requests=5, window_seconds=60)


def sanitize_search_input(query: str) -> str:
    query = query.strip()
    query = re.sub(r'[<>"\';\\]', '', query)
    return query[:200]


def sanitize_path_input(path: str) -> str:
    path = path.strip()
    path = re.sub(r'[\\/:*?"<>|;`&$!]', '', path)
    path = re.sub(r'\s+', ' ', path).strip()
    return path[:500]
