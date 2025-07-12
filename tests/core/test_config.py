# tests/core/test_config.py

import os
import pytest
from pydantic import ValidationError
from unittest.mock import patch

# Import both the class, the instance factory, AND the constant
from app.core.config import Settings, get_settings, BASE_DIR

def test_settings_load_from_test_env_file():
    """
    Test that settings load correctly from the .env.test file.
    """
    settings = get_settings()
    assert settings.ENVIRONMENT == "test"
    assert settings.DB_NAME == "defaultdb_test"

def test_settings_missing_required_env_vars(monkeypatch):
    """ Test that Settings loading fails if a required env var is missing """
    monkeypatch.delenv("DB_HOST", raising=False)
    # Ensure other required fields are present to isolate the error
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_USER", "dummy_user")
    monkeypatch.setenv("DB_PASSWORD", "dummy_pw")
    monkeypatch.setenv("DB_NAME", "dummy_db")
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_KEY_PATH", "dummy.json")

    with pytest.raises(ValidationError):
        Settings() # Try to initialize directly to catch validation error

def test_firebase_path_interpretation():
    """ Test that the Firebase key path is read correctly from settings """
    settings = get_settings()
    assert settings.FIREBASE_SERVICE_ACCOUNT_KEY_PATH == "service-account.json"
    
    # Correctly use the imported BASE_DIR constant, not settings.BASE_DIR
    constructed_path = os.path.join(BASE_DIR, settings.FIREBASE_SERVICE_ACCOUNT_KEY_PATH)
    assert os.path.isabs(constructed_path)