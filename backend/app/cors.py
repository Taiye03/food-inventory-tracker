"""
CORS that uses exactly the origins validated at startup (P0-7).

Starlette builds its middleware when the server first handles something, so
passing an origin list in at import time would freeze whatever the
environment held when app.main was imported, even if it changed before the
server started. Validating inside the middleware isn't safe either: an error
there happens outside the startup handler, and uvicorn's default
(lifespan="auto") then treats startup as "unsupported" and keeps serving.

So validation stays in startup (app.main.lifespan, where a failure stops the
server), which stores the validated origins in app.state; this middleware
reads them on every request. Until startup has succeeded, and after
shutdown, no origin is allowed.
"""
from starlette.datastructures import State
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp


class StartupConfiguredCORSMiddleware(CORSMiddleware):
    def __init__(self, app: ASGIApp, *, state: State):
        # No origins given here: every allow/deny decision in Starlette's
        # CORSMiddleware goes through is_allowed_origin() below.
        super().__init__(app, allow_origins=(), allow_credentials=True,
                         allow_methods=["*"], allow_headers=["*"])
        self._state = state

    def is_allowed_origin(self, origin: str) -> bool:
        return origin in getattr(self._state, "frontend_origins", ())
