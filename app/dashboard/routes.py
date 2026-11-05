"""Serve the dashboard UI at /dashboard."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return _TEMPLATES.TemplateResponse(request, "index.html", {})