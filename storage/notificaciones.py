"""
Notificaciones de escritorio via plyer (Windows/Linux/Mac).
Falla silenciosamente si no hay entorno grafico o plyer no esta instalado.
"""

import logging

logger = logging.getLogger(__name__)


def notificar(titulo: str, mensaje: str, timeout: int = 10) -> None:
    try:
        from plyer import notification
        notification.notify(
            title=titulo,
            message=mensaje,
            app_name="SRI Scraper - Banco Guayaquil",
            timeout=timeout,
        )
    except Exception as e:
        logger.debug("Notificacion de escritorio no disponible: %s", e)
