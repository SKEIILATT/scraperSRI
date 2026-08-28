"""
Validacion de colaboradores del banco contra STAGING.DBO.DW_DIM_COLABORADORES.

Para activar el bypass temporal: AUTH_BYPASS_MODE=true en .env
Para conectar la base real: AUTH_BYPASS_MODE=false y configurar DB_* en .env
Para exigir centro de costo autorizado: VERIFICAR_AREA=true en .env
"""

import logging
import re
from dataclasses import dataclass

from config import settings

logger = logging.getLogger(__name__)

# Centros de costo autorizados cuando VERIFICAR_AREA=true
ALLOWED_CENTROS_COSTO = {
    "TRIBU ANALITICA Y DATA",
    "COE ANALITICA Y DATA",
}

# Usuarios especiales que evitan el filtro de centro de costos, aun con VERIFICAR_AREA=true
SPECIAL_AUTHORIZED_USERS = {
    ("22993", "DRAMIREZ1"),
}


@dataclass
class Colaborador:
    codigo: str
    usuario: str
    nombre_completo: str = ""


def validar_formato(codigo: str, usuario: str) -> tuple[bool, str]:
    """Valida formato de los campos antes de consultar la base."""
    if not codigo or not codigo.strip():
        return False, "El código de trabajador es obligatorio."
    if not re.match(r"^\d+$", codigo.strip()):
        return False, "El código de trabajador debe ser numérico."
    if not usuario or not usuario.strip():
        return False, "El nombre de usuario es obligatorio."
    if not re.match(r"^[a-zA-Z0-9]+$", usuario.strip()):
        return False, "El nombre de usuario solo puede contener letras y números, sin espacios."
    return True, ""


def validar_colaborador(codigo: str, usuario: str) -> tuple[bool, str, Colaborador | None]:
    """
    Verifica si el colaborador existe en la base de datos del banco, si el
    nombre de usuario coincide y, si VERIFICAR_AREA esta activo, si su centro
    de costo esta autorizado.

    Retorna (True, "", Colaborador) si es valido, o (False, mensaje_error, None) si no.
    """
    ok, msg = validar_formato(codigo, usuario)
    if not ok:
        return False, msg, None

    if settings.AUTH_BYPASS_MODE:
        logger.warning(
            "AUTH_BYPASS_MODE activo: login de colaborador aceptado sin validar BD. "
            "Colaborador: codigo=%s usuario=%s",
            codigo,
            usuario,
        )
        return True, "", Colaborador(codigo=codigo.strip(), usuario=usuario.strip().upper())

    return _validar_contra_base_real(codigo.strip(), usuario.strip())


def _validar_contra_base_real(codigo: str, usuario: str) -> tuple[bool, str, Colaborador | None]:
    """Consulta real a SQL Server. Solo se llama cuando AUTH_BYPASS_MODE=false."""
    try:
        import pyodbc  # importacion diferida: no falla si no esta instalado en modo bypass

        with pyodbc.connect(settings.get_connection_string(), timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT CODIGO_EMPLEADO, USUARIO, CENTRO_COSTO, NOMBRE "
                "FROM STAGING.DBO.DW_DIM_COLABORADORES WITH (NOLOCK) "
                "WHERE CODIGO_EMPLEADO = ?",
                codigo,
            )
            row = cursor.fetchone()

    except ImportError:
        logger.error("pyodbc no esta instalado. Instalar con: pip install pyodbc")
        return False, "Error de configuración interna. Contacte al administrador.", None
    except Exception as exc:
        logger.exception("Error consultando BD de colaboradores: %s", exc)
        return False, "No se pudo verificar el acceso. Intente nuevamente.", None

    if row is None:
        logger.warning("Codigo %s no encontrado en DW_DIM_COLABORADORES", codigo)
        return False, "Código no encontrado en los datos de OGA.", None

    db_codigo, db_usuario, db_centro_costo, db_nombre = row
    db_usuario = str(db_usuario).strip().upper()

    if db_usuario != usuario.upper():
        logger.warning(
            "Usuario %s no coincide con el registrado para codigo %s (esperado: %s)",
            usuario, codigo, db_usuario,
        )
        return False, "Usuario incorrecto para el código ingresado.", None

    if settings.VERIFICAR_AREA:
        centro_costo = str(db_centro_costo or "").strip().upper()
        user_key = (str(db_codigo).strip(), db_usuario)
        if user_key not in SPECIAL_AUTHORIZED_USERS and centro_costo not in ALLOWED_CENTROS_COSTO:
            logger.warning(
                "Usuario %s (%s) pertenece al centro de costo '%s', que no esta autorizado.",
                db_usuario, codigo, centro_costo or "<vacio>",
            )
            return False, "No estás autorizado.", None

    colaborador = Colaborador(
        codigo=str(db_codigo).strip(),
        usuario=db_usuario,
        nombre_completo=str(db_nombre or "").strip(),
    )
    logger.info("Usuario autenticado: %s (%s)", colaborador.usuario, colaborador.codigo)
    return True, "", colaborador
