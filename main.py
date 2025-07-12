# backend/main.py
# import logging # Removed: Logging configuration is now handled by app.core.logging
import os
import uuid # Import the uuid library
from contextlib import asynccontextmanager

import asyncpg
import firebase_admin
import sentry_sdk
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from firebase_admin import credentials
from sentry_sdk.integrations.asyncpg import AsyncPGIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.rate_limit import limiter

# --- Core App Imports ---
from app.core.config import settings, BASE_DIR  # Centralized settings
# Import the new logging setup module early to configure logging before other imports
import app.core.logging

# Get the logger instance for this module AFTER logging is configured
# logger = logging.getLogger(__name__) # Removed: Use the logger configured via app.core.logging
logger = app.core.logging.get_logger(__name__) # Get the logger for this module

from app.db.base import close_db_pool, init_db_pool # DB Pool management
# --- API Router Imports ---
from app.api.endpoints import users as users_module
from app.api.endpoints import lists as lists_router
from app.api.endpoints import discovery as discovery_router
from app.api.endpoints import collaborators as collab_router

logger.info(f"Starting application in {settings.ENVIRONMENT} mode...")

# --- Sentry Initialization ---
if settings.SENTRY_DSN and settings.ENVIRONMENT != "development": # Often disabled in dev
    try:
        logger.info("Initializing Sentry...")
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            traces_sample_rate=0.2, # Sample 20% of transactions in production
            profiles_sample_rate=0.1, # Sample 10% of profiles in production
            environment=settings.ENVIRONMENT,
            integrations=[
                StarletteIntegration(),
                FastApiIntegration(),
                AsyncPGIntegration(),
            ],
            send_default_pii=False
        )
        logger.info(f"Sentry initialized successfully for environment: {settings.ENVIRONMENT}")
    except Exception as e:
        logger.error(f"Failed to initialize Sentry: {e}", exc_info=True)
else:
    logger.warning("Sentry DSN not found or ENVIRONMENT is development, Sentry integration disabled.")

# --- Firebase Admin SDK Initialization ---
try:
    # In a test environment, we rely on mocks and don't need to initialize the real SDK.
    if settings.ENVIRONMENT == "test":
        logger.warning("Skipping Firebase Admin SDK initialization in 'test' environment. Auth will be mocked.")
    else:
        # For all other environments (dev, prod, etc.), initialize the SDK.
        if not firebase_admin._apps:
            # Use the reliable BASE_DIR from settings to construct the path.
            cred_path = os.path.join(BASE_DIR, settings.FIREBASE_SERVICE_ACCOUNT_KEY_PATH)
            logger.info(f"Attempting to load Firebase credentials from: {cred_path}")

            if not os.path.exists(cred_path):
                logger.critical(f"Firebase service account key not found at: {cred_path}")
                raise FileNotFoundError(f"Service account key not found: {cred_path}")

            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            logger.info("Firebase Admin SDK initialized successfully.")
        else:
            logger.info("Firebase Admin SDK already initialized.")

except Exception as e:
    # This block will catch any exception from the 'else' part above.
    # It will NOT be triggered in a 'test' environment.
    logger.critical(f"CRITICAL: Failed during Firebase Admin SDK setup: {e}", exc_info=True)
    # Raising the exception is better than exit(1) as it can be caught by the lifespan manager.
    raise RuntimeError("Could not initialize Firebase Admin SDK.") from e


# --- Lifespan Manager (Handles DB Pool) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles application startup and shutdown events."""
    # In a test environment, the DB pool is managed by the session-scoped pytest fixture.
    # We prevent the app's lifespan from managing the pool to avoid conflicts.
    if settings.ENVIRONMENT == "test":
        logger.warning("Test environment detected. Skipping lifespan DB pool management.")
        yield
        return

    logger.info("Application startup sequence initiated...")
    try:
        await init_db_pool()
    except Exception as e:
        logger.critical(f"Failed to initialize DB pool during startup: {e}", exc_info=True)
        # In non-test environments, database failure is critical.
        logger.critical("CRITICAL: Database pool initialization failed. Exiting.")
        exit(1)

    logger.info("Application startup complete.")
    yield # Application runs here
    # Shutdown: Close Database Pool
    logger.info("Application shutdown sequence initiated...")
    await close_db_pool()
    logger.info("Application shutdown complete.")

# --- FastAPI App Instance ---
app = FastAPI(
    title="Sesame App API",
    version="1.0.0", # Consider pulling from config or pyproject.toml
    lifespan=lifespan,
    # Configure OpenAPI documentation URL if using API prefix
    # openapi_url=f"{settings.API_V1_STR}/openapi.json",
    # docs_url=f"{settings.API_V1_STR}/docs",
    # redoc_url=f"{settings.API_V1_STR}/redoc",
)

# --- Rate Limiting ---
app.state.limiter = limiter # Add limiter to app state for use in endpoints
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler) # Handle rate limit errors

# --- Middleware ---
# Optional: CORS Middleware (Uncomment and configure if needed)
# from fastapi.middleware.cors import CORSMiddleware
# if settings.BACKEND_CORS_ORIGINS:
#     app.add_middleware(
#         CORSMiddleware,
#         allow_origins=[str(origin).strip('/') for origin in settings.BACKEND_CORS_ORIGINS],
#         allow_credentials=True,
#         allow_methods=["*"],
#         allow_headers=["*"],
#     )
#     logger.info(f"CORS enabled for origins: {settings.BACKEND_CORS_ORIGINS}")

@app.middleware("http")
async def log_request_middleware(request: Request, call_next):
    """Logs basic request and response info and adds/uses a request ID."""
    # Get X-Request-ID from header or generate a new one (UUID4)
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    # Add the request ID to the State for potential use in dependencies/endpoints (e.g., for specific logging)
    request.state.request_id = request_id

    # Use the global logger instance
    logger.info(f"RID:{request_id} START Request: {request.method} {request.url.path}")
    try:
        response: Response = await call_next(request)
        # Add request ID to response headers for tracing
        response.headers["X-Request-ID"] = request_id
        logger.info(f"RID:{request_id} END Request: {request.method} {request.url.path} Status: {response.status_code}")
        return response
    except Exception as e:
        logger.error(f"RID:{request_id} Error during request {request.url.path}: {e}", exc_info=True)
        # Re-raise to be caught by exception handlers
        raise e

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Adds basic security headers to responses."""
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # Consider adding CSP header carefully if needed:
    # response.headers["Content-Security-Policy"] = "default-src 'self'; object-src 'none';"
    # HSTS header is best applied at the reverse proxy/load balancer level
    return response

# --- Global Exception Handlers ---
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom handler for HTTPExceptions to ensure consistent JSON format."""
    request_id = getattr(request.state, 'request_id', 'N/A')
    # Use the global logger instance
    logger.warning(f"RID:{request_id} HTTPException: Status={exc.status_code}, Detail={exc.detail} for {request.method} {request.url.path}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=exc.headers,
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    request_id = getattr(request.state, "request_id", "N/A")

    errors = jsonable_encoder(exc.errors())

    logger.error(
        f"RID:{request_id} Validation error for request {request.method} {request.url}: {errors}",
        exc_info=False,
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Validation Error", "errors": errors},
    )

@app.exception_handler(asyncpg.PostgresError)
async def db_exception_handler(request: Request, exc: asyncpg.PostgresError):
    """Handles database errors, logging details and returning a generic 500."""
    request_id = getattr(request.state, 'request_id', 'N/A')
    # Use the global logger instance
    logger.error(f"RID:{request_id} Database error during request {request.method} {request.url}: SQLSTATE={exc.sqlstate} - {exc}", exc_info=True)
    # Specific checks for user-facing messages (avoid exposing too much detail)
    if isinstance(exc, asyncpg.exceptions.UniqueViolationError):
        # Can customize based on constraint name if needed (exc.constraint_name)
        # For uniqueness, 409 is often appropriate, but may depend on the context
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": "A related resource already exists or there is a conflict."})
    # Add more specific handlers if needed (e.g., foreign key violation -> 400/409 depending on context)
    # Return generic error for other DB issues
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "A database error occurred processing your request."}
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Handles any other unexpected errors."""
    # HTTPExceptions are already handled above
    request_id = getattr(request.state, 'request_id', 'N/A')
    # Use the global logger instance
    logger.error(f"RID:{request_id} Unhandled exception during request {request.method} {request.url}: {type(exc).__name__} - {exc}", exc_info=True)
    # Manually capture in Sentry if it wasn't automatically captured (FastAPI integration usually does)
    # sentry_sdk.capture_exception(exc) # Might be redundant if FastAPI integration captures globally
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred."}
    )


# --- Include API Routers ---
# Add the main API routers with a prefix (e.g., /api/v1)
app.include_router(users_module.router, prefix=settings.API_V1_STR)
app.include_router(lists_router.router,  prefix=f"{settings.API_V1_STR}/lists")
app.include_router(discovery_router.router, prefix=settings.API_V1_STR, tags=["Discovery"])
app.include_router(users_module.notifications_router, prefix=settings.API_V1_STR)
app.include_router(collab_router.router, prefix=f"{settings.API_V1_STR}/lists", tags=["Collaboration"])


# --- Root Endpoint ---
@app.get("/", include_in_schema=False) # Exclude from OpenAPI docs if desired
async def read_root():
    """Provides a simple welcome message at the root."""
    return {"message": f"Welcome to the {app.title}!"}

# --- Sentry Debug Endpoint (Conditional) ---
# Only include in schema documentation AND enable if in development mode
@app.get(
    "/sentry-debug",
    tags=["Debug"],
    include_in_schema=settings.ENVIRONMENT == "development", # Condition based on setting
    response_model=dict # Define a response model for clarity
)
async def trigger_sentry_error():
    """Endpoint to test Sentry integration by causing a division by zero error."""
    if settings.ENVIRONMENT != "development":
         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not available in this environment.")
    # Use the global logger instance
    logger.info("Triggering Sentry test error...")
    # Trigger an error that will be caught by the global exception handler
    # This tests Sentry's automatic integration.
    division_by_zero = 1 / 0
    return {"message": "This should not be reached."}

# Note: The __main__ block for running with uvicorn directly is removed
# as it's better practice to run via the command line:
# uvicorn main:app --reload --host 0.0.0.0 --port 8000