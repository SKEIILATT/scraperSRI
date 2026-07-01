"""
Manejo seguro de credenciales SRI via Windows Credential Manager (DPAPI).

Las credenciales se cifran con la cuenta de Windows del usuario actual.
Nadie puede leerlas sin estar autenticado en esa sesion de Windows.
"""

import logging

logger = logging.getLogger(__name__)

_SERVICE = "sri-scraper-banco-guayaquil"


def guardar(ruc: str, password: str) -> None:
    import keyring
    keyring.set_password(_SERVICE, ruc, password)
    logger.info("Credenciales guardadas en Credential Manager para RUC %s", ruc)


def obtener(ruc: str) -> str | None:
    import keyring
    return keyring.get_password(_SERVICE, ruc)


def eliminar(ruc: str) -> None:
    try:
        import keyring
        keyring.delete_password(_SERVICE, ruc)
        logger.info("Credenciales eliminadas del Credential Manager para RUC %s", ruc)
    except Exception:
        pass


def tiene_credenciales(ruc: str) -> bool:
    return obtener(ruc) is not None
