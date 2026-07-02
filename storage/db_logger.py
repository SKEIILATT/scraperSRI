"""
Registro de sesiones de descarga en T_COMPROBANTES_SRI.

Tabla asumida en STAGING.DBO (misma BD que DW_DIM_COLABORADORES):

  T_COMPROBANTES_SRI (
      ID                    INT           IDENTITY(1,1) PRIMARY KEY,
      FECHA_DESCARGA        DATETIME      NOT NULL,
      RUC_CONTRIBUYENTE     VARCHAR(20)   NOT NULL,
      PERIODO_ANIO          SMALLINT      NOT NULL,
      PERIODO_MES           TINYINT       NOT NULL,
      TIPO_COMPROBANTE_COD  VARCHAR(5)    NOT NULL,
      TIPO_COMPROBANTE_DESC VARCHAR(100)  NULL,
      TOTAL_ENCONTRADOS     INT           NOT NULL DEFAULT 0,
      TOTAL_DESCARGADOS     INT           NOT NULL DEFAULT 0,
      TOTAL_ERRORES         INT           NOT NULL DEFAULT 0,
      CARPETA_DESTINO       VARCHAR(500)  NULL,
      ESTADO                VARCHAR(20)   NOT NULL,   -- COMPLETADO | PARCIAL | CANCELADO | ERROR
      CODIGO_COLABORADOR    VARCHAR(50)   NULL,
      USUARIO_COLABORADOR   VARCHAR(100)  NULL
  )
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from config import settings

logger = logging.getLogger(__name__)

_INSERT_SQL = """
INSERT INTO STAGING.DBO.T_COMPROBANTES_SRI (
    FECHA_DESCARGA, RUC_CONTRIBUYENTE, PERIODO_ANIO, PERIODO_MES,
    TIPO_COMPROBANTE_COD, TIPO_COMPROBANTE_DESC,
    TOTAL_ENCONTRADOS, TOTAL_DESCARGADOS, TOTAL_ERRORES,
    CARPETA_DESTINO, ESTADO, CODIGO_COLABORADOR, USUARIO_COLABORADOR
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


@dataclass
class RegistroSesion:
    ruc_contribuyente: str
    periodo_anio: int
    periodo_mes: int
    tipo_comprobante_cod: str
    tipo_comprobante_desc: str
    total_encontrados: int
    total_descargados: int
    total_errores: int
    carpeta_destino: str
    estado: str                      # COMPLETADO | PARCIAL | CANCELADO | ERROR
    codigo_colaborador: str = ""
    usuario_colaborador: str = ""


def registrar_sesion(registro: RegistroSesion) -> None:
    """
    Inserta un registro en T_COMPROBANTES_SRI.
    En modo bypass o sin DB disponible, solo loguea el registro y continua.
    """
    if settings.AUTH_BYPASS_MODE:
        logger.info(
            "[SIMULACION DB] T_COMPROBANTES_SRI <- RUC=%s anio=%d mes=%d tipo=%s "
            "encontrados=%d descargados=%d errores=%d estado=%s colaborador=%s/%s",
            registro.ruc_contribuyente,
            registro.periodo_anio,
            registro.periodo_mes,
            registro.tipo_comprobante_cod,
            registro.total_encontrados,
            registro.total_descargados,
            registro.total_errores,
            registro.estado,
            registro.codigo_colaborador,
            registro.usuario_colaborador,
        )
        return

    _insertar_en_db(registro)


def consultar_sesiones(ruc_filtro: str = "", codigo_colaborador: str = "", limite: int = 200) -> list[dict]:
    """
    Retorna las sesiones registradas en T_COMPROBANTES_SRI ordenadas por fecha descendente,
    restringidas al colaborador indicado (cada usuario solo ve sus propias descargas).
    En modo bypass retorna lista vacia.
    """
    if settings.AUTH_BYPASS_MODE:
        return []
    if not codigo_colaborador:
        return []
    return _consultar_db(ruc_filtro.strip(), codigo_colaborador.strip(), limite)


def _consultar_db(ruc_filtro: str, codigo_colaborador: str, limite: int) -> list[dict]:
    try:
        import pyodbc

        sql = (
            "SELECT TOP (?) ID, FECHA_DESCARGA, RUC_CONTRIBUYENTE, PERIODO_ANIO, PERIODO_MES, "
            "TIPO_COMPROBANTE_DESC, TOTAL_ENCONTRADOS, TOTAL_DESCARGADOS, TOTAL_ERRORES, "
            "ESTADO, CARPETA_DESTINO, CODIGO_COLABORADOR, USUARIO_COLABORADOR "
            "FROM STAGING.DBO.T_COMPROBANTES_SRI "
            "WHERE CODIGO_COLABORADOR = ? "
        )
        params: list = [limite, codigo_colaborador]
        if ruc_filtro:
            sql += "AND RUC_CONTRIBUYENTE = ? "
            params.append(ruc_filtro)
        sql += "ORDER BY FECHA_DESCARGA DESC"

        with pyodbc.connect(settings.get_connection_string(), timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, *params)
            cols = [c[0] for c in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    except Exception as exc:
        logger.warning("No se pudo consultar T_COMPROBANTES_SRI: %s", exc)
        return []


def _insertar_en_db(registro: RegistroSesion) -> None:
    try:
        import pyodbc

        with pyodbc.connect(settings.get_connection_string(), timeout=30) as conn:
            conn.cursor().execute(
                _INSERT_SQL,
                datetime.now(),
                registro.ruc_contribuyente,
                registro.periodo_anio,
                registro.periodo_mes,
                registro.tipo_comprobante_cod,
                registro.tipo_comprobante_desc,
                registro.total_encontrados,
                registro.total_descargados,
                registro.total_errores,
                registro.carpeta_destino or "",
                registro.estado,
                registro.codigo_colaborador or None,
                registro.usuario_colaborador or None,
            )
            conn.commit()

        logger.info(
            "Sesion registrada en T_COMPROBANTES_SRI: RUC=%s periodo=%d/%d estado=%s",
            registro.ruc_contribuyente,
            registro.periodo_mes,
            registro.periodo_anio,
            registro.estado,
        )
    except ImportError:
        logger.error("pyodbc no instalado — registro de sesion omitido.")
    except Exception as exc:
        logger.warning("No se pudo registrar la sesion en BD (no critico): %s", exc)
