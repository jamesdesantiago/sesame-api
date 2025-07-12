from datetime import datetime
from typing import List
from pydantic import BaseModel, Field

class NotificationItem(BaseModel):
    id: int
    title: str
    message: str
    created_at: datetime = Field(..., alias="createdAt")
    is_read: bool = Field(..., alias="isRead")

class PaginatedNotificationResponse(BaseModel):
    items: List[NotificationItem]
    page: int
    page_size: int = Field(..., alias="pageSize")
    total_items: int = Field(..., alias="totalItems")
    total_pages: int = Field(..., alias="totalPages")
