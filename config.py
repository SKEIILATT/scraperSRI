import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Auth
    AUTH_BYPASS_MODE: bool = os.getenv("AUTH_BYPASS_MODE", "true").lower() == "true"

    # Database
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", "1433"))
    DB_NAME: str = os.getenv("DB_NAME", "STAGING")
    DB_USER: str = os.getenv("DB_USER", "sa")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")

    # App
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = int(os.getenv("APP_PORT", "8080"))
    APP_TITLE: str = os.getenv("APP_TITLE", "SRI Scraper - Banco Guayaquil")
    APP_SECRET_KEY: str = os.getenv("APP_SECRET_KEY", "change_me")

    # Playwright
    BROWSER_PROFILE_DIR: str = os.getenv("BROWSER_PROFILE_DIR", "./browser_profile")
    BROWSER_HEADLESS: bool = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"

    # Downloads
    DOWNLOADS_BASE_DIR: str = os.getenv("DOWNLOADS_BASE_DIR", "./downloads")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
