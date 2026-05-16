import uuid
from datetime import datetime
from sqlalchemy import ForeignKey, String, DateTime, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from api.db.base import Base


class ProcessingLog(Base):
    __tablename__ = "processing_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    recording_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recording_jobs.id"), nullable=True
    )
    level: Mapped[str] = mapped_column(String, nullable=False)  # info | warning | error
    message: Mapped[str] = mapped_column(String, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=text("NOW()")
    )

    job: Mapped["RecordingJob | None"] = relationship("RecordingJob", back_populates="logs")
