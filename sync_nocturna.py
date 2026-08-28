"""
Sincronizacion nocturna de comprobantes SRI.

Registrar en el orquestador externo apuntando a este archivo.
Ejemplo de ejecucion directa:
    python sync_nocturna.py

Que hace:
  1. Consulta T_COMPROBANTES_SRI para obtener todos los RUC+periodo de los ultimos
     MESES_A_VERIFICAR meses.
  2. Para cada combinacion, recupera la contrasena desde Windows Credential Manager.
  3. Corre el scraper y descarga unicamente los archivos que no existen aun en disco.
  4. Registra el resultado en el log y actualiza la tabla.
"""

import asyncio
import logging
import sys
from datetime import datetime, date
from pathlib import Path

# Asegurar que el proyecto sea importable cuando el orquestador lo ejecuta desde otro directorio
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from config import settings
from storage.credentials import obtener, obtener_ci_adicional, tiene_credenciales
from sri_scraper.orchestrator import ScrapingOrchestrator, ParametrosConsulta, EstadoProceso

# ── Configuracion ─────────────────────────────────────────────────────────────
# Cuantos meses hacia atras verificar (incluyendo el mes actual)
MESES_A_VERIFICAR: int = 3

# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sync_nocturna")


def _periodos_a_verificar() -> list[tuple[int, int]]:
    """Retorna lista de (anio, mes) de los ultimos MESES_A_VERIFICAR meses."""
    hoy = date.today()
    periodos = []
    anio, mes = hoy.year, hoy.month
    for _ in range(MESES_A_VERIFICAR):
        periodos.append((anio, mes))
        mes -= 1
        if mes == 0:
            mes = 12
            anio -= 1
    return periodos


def _consultar_rucs_activos(periodos: list[tuple[int, int]]) -> list[dict]:
    """
    Devuelve los registros distintos RUC+ANIO+MES+TIPO de la tabla
    que coincidan con los periodos a verificar.
    En AUTH_BYPASS_MODE retorna lista vacia (no hay BD real).
    """
    if settings.AUTH_BYPASS_MODE:
        logger.warning("AUTH_BYPASS_MODE activo — no hay BD real, sync omitida.")
        return []

    try:
        import pyodbc

        placeholders = ",".join(["(?,?)"] * len(periodos))
        params: list = []
        for anio, mes in periodos:
            params += [anio, mes]

        sql = (
            "SELECT DISTINCT RUC_CONTRIBUYENTE, PERIODO_ANIO, PERIODO_MES, "
            "TIPO_COMPROBANTE_COD "
            "FROM PROCESOS_BI.DBO.T_COMPROBANTES_SRI "
            f"WHERE (PERIODO_ANIO, PERIODO_MES) IN ({placeholders}) "
            "AND ESTADO NOT IN ('ERROR') "
            "ORDER BY RUC_CONTRIBUYENTE, PERIODO_ANIO DESC, PERIODO_MES DESC"
        )

        with pyodbc.connect(settings.get_connection_string(), timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, *params)
            cols = [c[0].lower() for c in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    except Exception as exc:
        logger.error("No se pudo consultar la BD: %s", exc)
        return []


async def _sincronizar_uno(ruc: str, anio: int, mes: int, tipo: str) -> None:
    try:
        password = obtener(ruc)
    except Exception as exc:
        # keyring/DPAPI puede fallar al correr como tarea programada sin sesion
        # interactiva de Windows cargada. No debe tumbar el resto del batch.
        logger.error(
            "No se pudo leer credenciales del Credential Manager para RUC %s: %s", ruc, exc,
        )
        return
    if not password:
        logger.warning(
            "Sin credenciales guardadas para RUC %s — omitiendo periodo %d/%02d tipo %s. "
            "El usuario debe guardarlas desde la UI.",
            ruc, anio, mes, tipo,
        )
        return

    try:
        ci_adicional = obtener_ci_adicional(ruc) or ""
    except Exception:
        # No critico: si falla la lectura, se continua sin cedula adicional
        # (equivale a que el contribuyente no tenga acceso delegado configurado).
        ci_adicional = ""

    logger.info("Sincronizando RUC=%s periodo=%d/%02d tipo=%s", ruc, anio, mes, tipo)

    params = ParametrosConsulta(
        ruc=ruc,
        password=password,
        ci_adicional=ci_adicional,
        anio=anio,
        mes=mes,
        dia=0,
        tipos_comprobante=[tipo],
    )

    nuevos = 0

    async def _on_progress(estado: EstadoProceso) -> None:
        nonlocal nuevos
        nuevos = estado.completados

    orquestador = ScrapingOrchestrator(params, _on_progress)
    estado = await orquestador.ejecutar()

    if estado.errores:
        logger.warning(
            "RUC=%s periodo=%d/%02d tipo=%s — %d nuevos, %d errores: %s",
            ruc, anio, mes, tipo, nuevos, len(estado.errores), estado.errores[:3],
        )
    else:
        logger.info(
            "RUC=%s periodo=%d/%02d tipo=%s — %d nuevos descargados.",
            ruc, anio, mes, tipo, nuevos,
        )

    if nuevos > 0:
        from storage.notificaciones import notificar
        notificar(
            "Sincronizacion nocturna",
            f"{nuevos} comprobante{'s' if nuevos != 1 else ''} nuevo{'s' if nuevos != 1 else ''} "
            f"para RUC {ruc} ({mes:02d}/{anio})",
        )


async def main() -> None:
    inicio = datetime.now()
    logger.info("=== Inicio sincronizacion nocturna (%s) ===", inicio.strftime("%Y-%m-%d %H:%M"))

    periodos = _periodos_a_verificar()
    logger.info("Periodos a verificar: %s", periodos)

    registros = _consultar_rucs_activos(periodos)
    if not registros:
        logger.info("Sin registros a sincronizar. Fin.")
        return

    logger.info("Combinaciones a procesar: %d", len(registros))

    for reg in registros:
        try:
            await _sincronizar_uno(
                ruc=reg["ruc_contribuyente"],
                anio=reg["periodo_anio"],
                mes=reg["periodo_mes"],
                tipo=reg["tipo_comprobante_cod"],
            )
        except Exception as exc:
            # Un fallo no controlado en una combinacion (ej. navegador crasheo,
            # perfil bloqueado) no debe cancelar el resto del batch nocturno.
            logger.error(
                "Fallo no controlado sincronizando RUC=%s periodo=%d/%02d tipo=%s: %s",
                reg["ruc_contribuyente"], reg["periodo_anio"], reg["periodo_mes"],
                reg["tipo_comprobante_cod"], exc,
            )

    duracion = (datetime.now() - inicio).total_seconds()
    logger.info("=== Fin sincronizacion nocturna (%.0fs) ===", duracion)


if __name__ == "__main__":
    asyncio.run(main())
