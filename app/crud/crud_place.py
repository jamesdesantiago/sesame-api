# backend/app/crud/crud_place.py
import asyncpg
import logging
from typing import List, Optional, Tuple, Dict, Any

from app.schemas import place as place_schemas

logger = logging.getLogger(__name__)

# --- Custom Exceptions ---
class PlaceNotFoundError(Exception):
    """Raised when a place is expected but not found."""
    pass
class PlaceAlreadyExistsError(Exception):
    """Raised when attempting to add a place that already exists in the list."""
    pass
class InvalidPlaceDataError(Exception):
    """Raised for data validation errors like check constraint violations."""
    pass
class DatabaseInteractionError(Exception):
    """Generic database interaction error."""
    pass


# --- CRUD Operations ---

async def get_places_by_list_id_paginated(db: asyncpg.Connection, list_id: int, page: int, page_size: int) -> Tuple[List[asyncpg.Record], int]:
    """Fetches paginated places belonging to a specific list."""
    offset = (page - 1) * page_size
    logger.debug(f"Fetching places for list {list_id}, page {page}, size {page_size}")
    try:
        # Count query
        count_query = "SELECT COUNT(*) FROM places WHERE list_id = $1"
        total_items = await db.fetchval(count_query, list_id) or 0

        if total_items == 0:
            return [], 0 # Return empty list and 0 total if no places

        # Fetch query - select fields needed by PlaceItem schema
        fetch_query = """
            SELECT id, name, address, latitude, longitude, rating, notes, visit_status, place_id -- Include place_id
            FROM places
            WHERE list_id = $1
            ORDER BY created_at DESC, id DESC -- Or by sequence, name, etc.
            LIMIT $2 OFFSET $3
        """
        places = await db.fetch(fetch_query, list_id, page_size, offset)
        logger.debug(f"Found {len(places)} places for list {list_id} (total: {total_items})")
        return places, total_items
    except Exception as e:
        logger.error(f"Error fetching paginated places for list {list_id}: {e}", exc_info=True)
        raise DatabaseInteractionError("Database error fetching places.") from e


async def add_place_to_list(db: asyncpg.Connection, list_id: int, place_in: place_schemas.PlaceCreate) -> asyncpg.Record:
    """Adds a place to a list."""
    logger.info(f"Adding place '{place_in.name}' (external ID: {place_in.placeId}) to list {list_id}")
    try:
        # Note: 'place_id' in schema is the external ID (e.g., Google Place ID)
        # The database 'id' column is the primary key auto-generated.
        created_place_record = await db.fetchrow(
            """
            INSERT INTO places (list_id, place_id, name, address, latitude, longitude, rating, notes, visit_status, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW())
            RETURNING id, name, address, latitude, longitude, rating, notes, visit_status -- Return fields needed by PlaceItem schema
            """,
            list_id, place_in.placeId, place_in.name, place_in.address, place_in.latitude, place_in.longitude,
            place_in.rating, place_in.notes, place_in.visitStatus
        )
        if not created_place_record:
            # This indicates a fundamental DB issue where INSERT didn't return the record
            raise DatabaseInteractionError("Failed to add place (no record returned from DB)")
        logger.info(f"Place '{place_in.name}' added to list {list_id} with DB ID: {created_place_record['id']}")
        return created_place_record
    except asyncpg.exceptions.UniqueViolationError as e:
        # Check if the violation is on the (list_id, place_id) constraint
        # The constraint name might vary, check your DB schema
        if 'places_list_id_place_id_key' in str(e) or 'places_list_id_place_id_idx' in str(e):
            logger.warning(f"Place with external ID '{place_in.placeId}' already exists in list {list_id}")
            raise PlaceAlreadyExistsError("Place already exists in this list") from e
        else: # Handle other potential unique violations if any
            logger.error(f"Unexpected UniqueViolationError adding place to list {list_id}: {e}", exc_info=True)
            raise DatabaseInteractionError("Database constraint violation adding place.") from e
    except asyncpg.exceptions.CheckViolationError as e:
        logger.warning(f"Check constraint violation adding place to list {list_id}: {e}", exc_info=True)
        # Extract specific constraint violation if possible for better error message
        constraint_name = getattr(e, 'constraint_name', 'unknown check constraint')
        raise InvalidPlaceDataError(f"Invalid data for place ({constraint_name}).") from e
    except Exception as e:
        logger.error(f"Unexpected error adding place to list {list_id}: {e}", exc_info=True)
        raise DatabaseInteractionError("Database error adding place.") from e


async def update_place(db: asyncpg.Connection, place_id: int, list_id: int, place_update_in: place_schemas.PlaceUpdate) -> asyncpg.Record:
    """Updates fields for a specific place within a list."""
    logger.info(f"Updating place {place_id} in list {list_id}")
    update_fields = place_update_in.model_dump(exclude_unset=True, by_alias=True)

    if not any(v is not None for v in update_fields.values()):
        logger.warning(f"Update place called for place {place_id} in list {list_id} with no fields to update.")
        current_place = await db.fetchrow(
             "SELECT id, name, address, latitude, longitude, rating, notes, visit_status FROM places WHERE id = $1 AND list_id = $2",
             place_id, list_id
         )
        if not current_place:
             raise PlaceNotFoundError("Place not found in this list.")
        return current_place

    set_clauses = []
    params = []
    param_index = 1
    for field_name, value in update_fields.items():
        set_clauses.append(f"{field_name} = ${param_index}")
        params.append(value)
        param_index += 1

    params.extend([place_id, list_id])
    sql = f"""
        UPDATE places SET {', '.join(set_clauses)}, updated_at = NOW()
        WHERE id = ${param_index} AND list_id = ${param_index + 1}
        RETURNING id, name, address, latitude, longitude, rating, notes, visit_status
        """

    try:
        # The database operation is the only thing that should be in the try block
        updated_place_record = await db.fetchrow(sql, *params)
    except asyncpg.exceptions.CheckViolationError as e:
        logger.warning(f"Check constraint violation updating place {place_id}: {e}", exc_info=True)
        raise InvalidPlaceDataError(f"Invalid data provided for update ({e.constraint_name}).") from e
    except Exception as e:
        logger.error(f"Error updating place {place_id} in list {list_id}: {e}", exc_info=True)
        raise DatabaseInteractionError("Database error updating place.") from e

    # The check for existence happens *after* the database operation
    if not updated_place_record:
        logger.warning(f"Failed to update place {place_id} (not found in list {list_id}?)")
        raise PlaceNotFoundError("Place not found in this list for update.")
    
    logger.info(f"Updated place {place_id} in list {list_id}")
    return updated_place_record


async def delete_place_from_list(db: asyncpg.Connection, place_id: int, list_id: int) -> bool:
    """Deletes a place by its DB ID, ensuring it belongs to the specified list."""
    logger.info(f"Attempting to delete place {place_id} from list {list_id}")
    try:
        status = await db.execute(
            "DELETE FROM places WHERE id = $1 AND list_id = $2",
            place_id, list_id
        )
        deleted_count = int(status.split(" ")[1])
        if deleted_count > 0:
            logger.info(f"Place {place_id} deleted from list {list_id}")
            return True
        else:
            logger.warning(f"Attempted to delete place {place_id} from list {list_id}, but it was not found.")
            return False
    except Exception as e:
        logger.error(f"Error deleting place {place_id} from list {list_id}: {e}", exc_info=True)
        raise DatabaseInteractionError("Database error deleting place.") from e