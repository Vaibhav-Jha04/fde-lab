"""Entry point for uvicorn: `uv run uvicorn clinic_api.main:app --port 8000`."""

from .app import create_app

app = create_app()
