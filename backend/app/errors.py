"""
Validation errors (HTTP 422) without the submitted values (P0-7).

FastAPI's default 422 response repeats each rejected value back as "input",
and for some errors (a missing field, or a rule that checks several fields)
"input" is the WHOLE request body, password included. That body can end up in
browser developer tools, proxies and support screenshots.

This handler sends the same response with "input" removed from every error,
on every endpoint, so a secret field added later is covered too. Everything
the frontend uses ("msg", plus "type", "loc" and "ctx") is unchanged.
"""
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

HIDDEN = ("input",)


def errors_without_input(errors) -> list[dict]:
    return [{k: v for k, v in e.items() if k not in HIDDEN} for e in errors]


async def validation_error_without_input(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors_without_input(exc.errors()))})
