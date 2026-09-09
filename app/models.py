from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    station_code: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        index=True,
        nullable=False,
    )

    station_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    region_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
