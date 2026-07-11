"""
Qanuni.AI — Production Server Runner
Uses Waitress to serve the Flask application in production on Windows.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Fix Windows terminal encoding
if sys.platform.startswith("win"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# Force Flask to run in production mode (non-debug)
os.environ["FLASK_DEBUG"] = "0"

from app import app, PORT

if __name__ == "__main__":
    from waitress import serve
    print(f"\n>>> Starting Qanuni.AI Waitress Production Server...")
    print(f"    Serving at: http://0.0.0.0:{PORT}")
    print(f"    Check logs in: data/logs/qanuni_server.log\n")
    
    serve(app, host="0.0.0.0", port=PORT, threads=8)
