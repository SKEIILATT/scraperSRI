"""
Punto de entrada unico de la aplicacion SRI Scraper - Banco Guayaquil.

Ejecutar:
    python main.py

O en produccion con systemd/supervisor apuntando a este archivo.
"""

import logging
import os
from pathlib import Path

from nicegui import app, ui

from config import settings

# Configurar logging antes de importar modulos que loguean
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Servir assets estaticos desde la carpeta del proyecto
app.add_static_files(url_path="/_assets", local_directory=str(Path(__file__).parent))

# Importar paginas (registra las rutas @ui.page)
from ui import pages as _ui_pages  # noqa: F401

# Asegurar que el directorio de descargas exista
Path(settings.DOWNLOADS_BASE_DIR).mkdir(parents=True, exist_ok=True)

if __name__ in {"__main__", "__mp_main__"}:
    logger.info("Iniciando SRI Scraper en %s:%d", settings.APP_HOST, settings.APP_PORT)
    logger.info("AUTH_BYPASS_MODE: %s", settings.AUTH_BYPASS_MODE)

    ui.run(
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        title=settings.APP_TITLE,
        storage_secret=settings.APP_SECRET_KEY,
        favicon=None,
        dark=False,
        reload=False,
    )
