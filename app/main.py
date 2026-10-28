from pathlib import Path
import os
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env', override=False)

from app.api.routes import router
from app.db import init_db

app = FastAPI(title='IncidentIQ', version='1.1.0')

@app.on_event('startup')
def startup() -> None:
    init_db()

app.include_router(router, prefix='/api')
app.mount('/static', StaticFiles(directory=str(ROOT / 'static')), name='static')

@app.get('/api/model')
def model_configuration():
    live = bool(os.getenv('GEMINI_API_KEY', '').strip())
    return {'configured_mode': 'gemini' if live else 'mock',
            'model': os.getenv('GEMINI_MODEL', 'gemini-2.5-flash') if live else None,
            'note': 'Configuration only. Per-run llm_completed/llm_failed events verify execution.'}

@app.get('/')
def index():
    return FileResponse(ROOT / 'static/index.html')
