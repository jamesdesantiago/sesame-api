# backend/app/core/logging.py
import logging
from app.core.config import settings # Import settings to use ENVIRONMENT

# Configure logging
# Use a basic config for simplicity, could be expanded later for file handlers, etc.
# Level based on environment, defaulting to INFO
log_level = logging.INFO
if settings.ENVIRONMENT == "development":
    log_level = logging.DEBUG
elif settings.ENVIRONMENT == "test":
    log_level = logging.DEBUG # Often useful to see DEBUG logs in tests
else:
     log_level = logging.INFO # Production/Staging default to INFO

# Set up the root logger or configure specific loggers
# Check if handlers already exist to avoid re-configuring in environments that might reload
if not logging.root.handlers:
    logging.basicConfig(level=log_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    # Optional: Configure handlers for specific loggers if needed

# Get a logger instance for this module (optional, but good practice)
logger = logging.getLogger(__name__)
logger.debug("Core logging configured.")

# You can define a function here to get loggers for other modules if preferred,
# but `logging.getLogger(__name__)` works directly after basicConfig.
def get_logger(name: str):
    """Helper to get a logger instance for a specific module."""
    return logging.getLogger(name)