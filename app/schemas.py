from pydantic import BaseModel


class StationLite(BaseModel):
    code: str
    name: str
