"""Arranca la API y la PWA:  python -m servidor  ->  http://127.0.0.1:8000"""

import uvicorn

from asistente import config

if __name__ == "__main__":
    uvicorn.run("servidor.api:crear_app", factory=True, host=config.SERVIDOR_HOST, port=config.SERVIDOR_PUERTO)
