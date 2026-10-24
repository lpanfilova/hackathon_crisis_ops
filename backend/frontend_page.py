"""Serve the dashboard page from the API's own origin.

``frontend/app.py`` sends ``Content-Security-Policy: connect-src 'self'``, so a
page served by the frontend's own server on :8000 cannot call an API on any
other port, and CORS headers on the API cannot override that. Serving the same
page from FastAPI puts page and API on one origin, so the page can call
``/state``, ``/plan``, ``/incidents/...`` with relative URLs.

The page is imported unchanged from ``frontend/app.py``. Run one server:

    uvicorn backend.api:app --port 8000      # then open http://127.0.0.1:8000
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from frontend.app import PAGE

router = APIRouter(include_in_schema=False)

# Same headers frontend/app.py's own server sends (CrisisOpsHandler._send), so
# the page keeps its security policy when served from here.
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
    ),
}


@router.get("/", response_class=HTMLResponse)
@router.get("/index.html", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    """The CrisisOps dashboard, same origin as the API."""
    return HTMLResponse(PAGE, headers=SECURITY_HEADERS)


@router.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)
