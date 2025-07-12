# app/core/config.py
import os
import ssl
from typing import Optional, Dict
from pydantic import validator, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
from functools import lru_cache

# Determine the base directory of the project (where app/ and main.py live)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# This will be used when running the app normally (e.g., with uvicorn)
# For tests, the conftest.py will handle loading .env.test
DOTENV = os.getenv("DOTENV_PATH", os.path.join(BASE_DIR, '.env'))
if os.path.exists(DOTENV):
    load_dotenv(dotenv_path=DOTENV)

class Settings(BaseSettings):
    # App Environment
    ENVIRONMENT: str = "development"
    API_V1_STR: str = "/api/v1"

    # Database Configuration
    model_config = SettingsConfigDict(
         case_sensitive=True,
         env_file_encoding = 'utf-8'
     )

    # Fields required from environment
    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str
    DB_SSL_MODE: str = "prefer"
    DB_CA_CERT_FILE: Optional[str] = None

    @property
    def DATABASE_URL(self) -> str:
        dsn = f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?sslmode={self.DB_SSL_MODE}"
        if self.DB_SSL_MODE in ['verify-ca', 'verify-full'] and self.DB_CA_CERT_FILE:
            # The path to the 'certs' dir is relative to the project root (BASE_DIR)
            ca_cert_path = os.path.join(BASE_DIR, 'certs', self.DB_CA_CERT_FILE)
            dsn = f"{dsn}&sslrootcert={ca_cert_path}"
        return dsn

    # Firebase
    FIREBASE_SERVICE_ACCOUNT_KEY_PATH: str = "service-account.json"

    # Sentry
    SENTRY_DSN: Optional[str] = None

# Use lru_cache to load settings only once in production/development,
# but for testing, we want to be able to reload it.
if os.getenv('ENVIRONMENT') == 'test':
    def get_settings() -> Settings:
        """Get settings without caching for tests."""
        return Settings()
else:
    @lru_cache()
    def get_settings() -> Settings:
        """Get cached settings for production/development."""
        return Settings()

# Create a singleton instance accessible throughout the app
settings = get_settings()