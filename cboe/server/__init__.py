# -*- coding: utf-8 -*-
"""검수 화면 (review screen): FastAPI server + static single-page app.

    python -m cboe.server <work> [--port 8765] [--host 127.0.0.1] [--no-browser]
"""
from .app import create_app

__all__ = ["create_app"]
