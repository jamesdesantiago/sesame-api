# app/schemas/place.py
from pydantic import BaseModel, Field, validator
from typing import Optional, List

# --- Place Schemas ---

# Base schema with common place fields
class PlaceBase(BaseModel):
    name: str = Field(..., max_length=200, description="Name of the place")
    address: str = Field(..., max_length=300, description="Formatted address of the place")
    latitude: float = Field(..., ge=-90, le=90, description="Latitude coordinate")
    longitude: float = Field(..., ge=-180, le=180, description="Longitude coordinate")
    rating: Optional[str] = Field(None, description="User-defined rating (e.g., MUST_VISIT, WORTH_VISITING)") # Or use Literal/Enum
    notes: Optional[str] = Field(None, max_length=1000, description="User's notes about the place")
    visitStatus: Optional[str] = Field(None, alias="visit_status", description="User's visit status (e.g., VISITED, WANT_TO_VISIT)")
    model_config = {"from_attributes": True, "populate_by_name": True}

    # Example validator if needed for visitStatus/rating
    # @validator('visitStatus')
    # def check_visit_status(cls, v):
    #     if v is not None and v not in ["VISITED", "WANT_TO_VISIT"]:
    #         raise ValueError('visitStatus must be one of: VISITED, WANT_TO_VISIT')
    #     return v

# Schema representing a place item as returned by the API (includes DB ID)
# Used in GET /lists/{id}/places response and POST /lists/{id}/places response
class PlaceItem(PlaceBase):
    id: int = Field(..., description="Unique database identifier for the place item in the list")

    model_config = {"from_attributes": True} # For Pydantic V2+

# Schema for creating a new place within a list (request body for POST /lists/{id}/places)
class PlaceCreate(PlaceBase):
    placeId: str = Field(..., description="External identifier for the place (e.g., Google Place ID)")
    model_config = {"populate_by_name": True}

# Schema for updating an existing place within a list (request body for PATCH /lists/{id}/places/{place_id})
class PlaceUpdate(BaseModel):
    notes: Optional[str] = Field(None, max_length=1000, description="Updated notes for the place")
    model_config = {"populate_by_name": True}
    # Add other potentially updatable fields here if the API supports them
    # visitStatus: Optional[str] = None
    # rating: Optional[str] = None

# Schema for the paginated response wrapper for places (e.g., GET /lists/{id}/places)
class PaginatedPlaceResponse(BaseModel):
    items: List[PlaceItem] = Field(..., description="The list of place items on the current page")
    page: int = Field(..., ge=1, description="The current page number")
    page_size: int = Field(..., ge=1, description="Number of items per page")
    total_items: int = Field(..., ge=0, description="Total number of places matching the query")
    total_pages: int = Field(..., ge=0, description="Total number of pages available")