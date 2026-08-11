"""Make the repo root importable so tests can `import backend.app.main`
without installing the project (the backend is run via uvicorn, not pip)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
