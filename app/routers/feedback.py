from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.telegram import send_telegram_message


router = APIRouter(prefix="/api/feedback", tags=["Feedback"])


class FeedbackRequest(BaseModel):
    message: str = Field(..., min_length=3, max_length=2000)
    rating: int | None = Field(default=None, ge=1, le=5)
    category: str = Field(default="General", max_length=50)


@router.post("")
async def submit_feedback(request: FeedbackRequest):
    message = (
        " IRCTC App Feedback\n\n"
        f"Category: {request.category}\n"
        f"Rating: {request.rating or 'Not provided'}\n\n"
        f"Message:\n{request.message}"
    )

    try:
        await send_telegram_message(message)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Failed to send feedback"
        ) from exc

    return {
        "success": True,
        "message": "Thank you for your feedback!"
    }
