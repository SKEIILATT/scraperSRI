"""
Manejo de carpetas de descarga, nombres de archivo y generacion de ZIP final.

Estructura de carpetas:
  downloads/{RUC}_{YYYY}_{MM:02d}/                    <- un solo tipo: todo plano aqui
  downloads/{RUC}_{YYYY}_{MM:02d}/{Tipo_Comprobante}/  <- varios tipos: uno por subcarpeta
"""

import logging
import re
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}


def sanitizar_nombre(texto: str) -> str:
    texto = texto.strip()
    texto = re.sub(r"[^\w\s\-]", "", texto)
    texto = re.sub(r"\s+", "_", texto)
    return texto[:200]


def carpeta_descarga(ruc: str, anio: int, mes: int, subcarpeta: str = "") -> Path:
    """
    Carpeta general del periodo. Si se pasa `subcarpeta` (nombre del tipo de
    comprobante), retorna una subcarpeta dentro de la general -- se usa cuando
    el usuario pide varios tipos de comprobante en una misma corrida, para que
    cada tipo quede separado en su propia carpeta en vez de mezclarse.
    """
    carpeta = Path(settings.DOWNLOADS_BASE_DIR) / f"{ruc}_{anio}_{mes:02d}"
    if subcarpeta:
        carpeta = carpeta / sanitizar_nombre(subcarpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def ruta_descarga(
    ruc: str, anio: int, mes: int, nombre_doc: str, extension: str,
    es_retencion: bool, subcarpeta: str = "",
) -> Path:
    return carpeta_descarga(ruc, anio, mes, subcarpeta) / f"{sanitizar_nombre(nombre_doc)}.{extension}"


def ruta_reporte(ruc: str, anio: int, mes: int, subcarpeta: str = "") -> Path:
    return carpeta_descarga(ruc, anio, mes, subcarpeta) / "reporte.txt"


