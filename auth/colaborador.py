"""
Validacion de colaboradores del banco contra STAGING.DBO.DW_DIM_COLABORADORES.

Para activar el bypass temporal: AUTH_BYPASS_MODE=true en .env
Para conectar la base real: AUTH_BYPASS_MODE=false y configurar DB_* en .env
"""

import logging
import re
from dataclasses import dataclass

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class Colaborador:
    codigo: str
    usuario: str


def validar_formato(codigo: str, usuario: str) -> tuple[bool, str]:
    """Valida formato de los campos antes de consultar la base."""
    if not codigo or not codigo.strip():
        return False, "El codigo de trabajador es obligatorio."
    if not re.match(r"^\d+$", codigo.strip()):
        return False, "El codigo de trabajador debe ser numerico."
    if not usuario or not usuario.strip():
        return False, "El nombre de usuario es obligatorio."
    if not re.match(r"^[a-zA-Z0-9]+$", usuario.strip()):
        return False, "El nombre de usuario solo puede contener letras y numeros, sin espacios."
    return True, ""


def validar_colaborador(codigo: str, usuario: str) -> tuple[bool, str]:
    """
    Verifica si el colaborador existe en la base de datos del banco.

    Retorna (True, "") si es valido, o (False, mensaje_error) si no.
    """
    ok, msg = validar_formato(codigo, usuario)
    if not ok:
        return False, msg

    # ----------------------------------------------------------------
    # TODO: BYPASS TEMPORAL - conectar a STAGING.DBO.DW_DIM_COLABORADORES
    # Cuando AUTH_BYPASS_MODE=false, esta funcion debe consultar:
    #   SELECT COUNT(*) FROM STAGING.DBO.DW_DIM_COLABORADORES
    #   WHERE CODIGO_TRABAJADOR = ? AND NOMBRE_USUARIO = ?
    # ----------------------------------------------------------------
    if settings.AUTH_BYPASS_MODE:
        logger.warning(
            "AUTH_BYPASS_MODE activo: login de colaborador aceptado sin validar BD. "
            "Colaborador: codigo=%s usuario=%s",
            codigo,
            usuario,
        )
        return True, ""

    return _validar_contra_base_real(codigo.strip(), usuario.strip())


def _validar_contra_base_real(codigo: str, usuario: str) -> tuple[bool, str]:
    """Consulta real a SQL Server. Solo se llama cuando AUTH_BYPASS_MODE=false."""
    try:
        import pyodbc  # importacion diferida: no falla si no esta instalado en modo bypass

        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={settings.DB_HOST},{settings.DB_PORT};"
            f"DATABASE={settings.DB_NAME};"
            f"UID={settings.DB_USER};"
            f"PWD={settings.DB_PASSWORD}"
        )
        with pyodbc.connect(conn_str, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM STAGING.DBO.DW_DIM_COLABORADORES "
                "WHERE CODIGO_TRABAJADOR = ? AND NOMBRE_USUARIO = ?",
                codigo,
                usuario,
            )
            count = cursor.fetchone()[0]

        if count > 0:
            return True, ""
        return False, "Codigo de trabajador o nombre de usuario incorrecto."

    except ImportError:
        logger.error("pyodbc no esta instalado. Instalar con: pip install pyodbc")
        return False, "Error de configuracion interna. Contacte al administrador."
    except Exception as exc:
        logger.exception("Error consultando BD de colaboradores: %s", exc)
        return False, "No se pudo verificar el acceso. Intente nuevamente."
