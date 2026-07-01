"""
Estado compartido de la sesion de usuario en NiceGUI.
Cada conexion (tab) tiene su propia instancia via app.storage.user.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SesionUsuario:
    # Login interno
    autenticado: bool = False
    codigo_colaborador: str = ""
    nombre_usuario: str = ""

    # Credenciales SRI (solo en memoria, nunca se persisten)
    sri_ruc: str = ""
    sri_password: str = ""

    # Parametros de consulta
    anio: int = 2024
    mes: int = 1
    dia: int = 0   # 0 = Todos
    tipo_comprobante: str = "1"

    # Estado del proceso
    proceso_activo: bool = False
    fase_proceso: str = ""
    total_comprobantes: int = 0
    completados: int = 0
    errores_proceso: list[str] = field(default_factory=list)
    zip_disponible: str = ""
    requiere_intervencion: bool = False
    mensaje_intervencion: str = ""
    proceso_finalizado: bool = False
    proceso_cancelado: bool = False

    # Paso actual del wizard (1-5)
    paso: int = 1
