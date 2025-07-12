# NEW FILE  ─ app/db/sqlalchemy_base.py
"""
Central place that
  • re-exports the Declarative Base
  • imports every model once so their tables register on Base.metadata
"""

from .base_class import Base

# ▸ import *every* model that defines a table
from app.schemas.user import User   # noqa: F401
from app.schemas.list import List   # noqa: F401
from app.schemas.place import Place # noqa: F401

from sqlalchemy.orm import declarative_base
Base = declarative_base()

__all__ = ["Base"]
