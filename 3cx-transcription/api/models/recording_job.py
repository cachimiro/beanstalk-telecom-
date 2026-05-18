import uuid
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, DateTime, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from api.db.base import Base


class RecordingJob(Base):
    __tablename__ = "recording_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    gcs_bucket: Mapped[str] = mapped_column(String, nullable=False)
    gcs_object_name: Mapped[str] = mapped_column(String, nullable=False)
    gcs_generation: Mapped[str | None] = mapped_column(String, nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Parsed fields
    extracted_name: Mapped[str | None] = mapped_column(String, nullable=True)
    folder_extension: Mapped[str | None] = mapped_column(String, nullable=True)
    filename_extension: Mapped[str | None] = mapped_column(String, nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String, nullable=True)
    call_timestamp: Mapped[str | None] = mapped_column(String, nullable=True)
    call_id: Mapped[str | None] = mapped_column(String, nullable=True)
    file_extension: Mapped[str | None] = mapped_column(String, nullable=True)

    # Routing
    matched_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    recipient_email: Mapped[str | None] = mapped_column(String, nullable=True)

    # Status
    status: Mapped[str] = mapped_column(String, nullable=False, default="received")
    summary_status: Mapped[str | None] = mapped_column(String, nullable=True)

    # Provider IDs
    assemblyai_transcript_id: Mapped[str | None] = mapped_column(String, nullable=True)
    email_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    email_transcript_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    summary_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_html: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Speaker re-classification
    speaker_confidence_score: Mapped[float | None] = mapped_column(nullable=True)
    speaker_classification_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String, nullable=True)

    # Error tracking
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=text("NOW()")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    matched_user: Mapped["User | None"] = relationship("User", back_populates="jobs")
    logs: Mapped[list["ProcessingLog"]] = relationship(
        "ProcessingLog", back_populates="job", cascade="all, delete-orphan"
    )
