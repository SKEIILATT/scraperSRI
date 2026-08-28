import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Auth
    AUTH_BYPASS_MODE: bool = os.getenv("AUTH_BYPASS_MODE", "true").lower() == "true"
    VERIFICAR_AREA: bool = os.getenv("VERIFICAR_AREA", "false").lower() == "true"

    # Database (autenticacion integrada de Windows, sin usuario/contrasena)
    DB_HOST: str = os.getenv("DB_HOST", "GYEINTNEGDB01")
    DB_PORT: int = int(os.getenv("DB_PORT", "4433"))
    DB_NAME: str = os.getenv("DB_NAME", "PROCESOS_BI")

    def get_connection_string(self) -> str:
        return (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={self.DB_HOST},{self.DB_PORT};"
            f"DATABASE={self.DB_NAME};"
            f"Trusted_Connection=yes;"
            f"TrustServerCertificate=yes;"
            f"Connection Timeout=30;"
        )

    # App
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    # PORT tiene prioridad: algunos launchers/orquestadores inyectan esta
    # variable de entorno al lanzar el proceso, distinta a la de .env.
    APP_PORT: int = int(os.getenv("PORT", os.getenv("APP_PORT", "8080")))
    APP_TITLE: str = os.getenv("APP_TITLE", "SRI Scraper - Banco Guayaquil")
    APP_SECRET_KEY: str = os.getenv("APP_SECRET_KEY", "change_me")

    # Playwright
    # expandvars() permite usar variables de entorno de Windows (ej. %LOCALAPPDATA%)
    # dentro del valor -- python-dotenv NO las expande por su cuenta. Esto es clave
    # cuando el proyecto vive en un disco compartido y varias personas corren la
    # app desde sus propias PCs con el MISMO .env: cada una necesita su propio
    # perfil de navegador local (Chrome/Edge no soportan un perfil persistente
    # compartido por red -- se bloquea con uso concurrente y arriesga corromper
    # las bases SQLite internas del perfil vía SMB). Con %LOCALAPPDATA% en la
    # ruta, el mismo .env compartido resuelve a una carpeta local distinta en
    # cada maquina/usuario sin necesitar configuracion por persona.
    BROWSER_PROFILE_DIR: str = os.path.expandvars(os.getenv("BROWSER_PROFILE_DIR", "./browser_profile"))
    BROWSER_HEADLESS: bool = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"
    # Ruta explicita a un Chrome/Edge ya instalado (evita depender de la
    # deteccion de canal por registro de Windows, poco confiable en equipos
    # corporativos restringidos, y evita descargar el Chromium bundled).
    BROWSER_EXECUTABLE_PATH: str = os.getenv("BROWSER_EXECUTABLE_PATH", "")
    # Rutas candidatas si BROWSER_EXECUTABLE_PATH no esta definido explicitamente.
    BROWSER_EXECUTABLE_CANDIDATES: list[str] = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]

    # Downloads
    DOWNLOADS_BASE_DIR: str = os.getenv("DOWNLOADS_BASE_DIR", "./downloads")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
