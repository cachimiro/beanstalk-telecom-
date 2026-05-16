"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("full_name", sa.Text, nullable=False),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("extension", sa.Text, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")),
    )
    # Only one active user per extension
    op.create_index(
        "unique_active_extension",
        "users",
        ["extension"],
        unique=True,
        postgresql_where=sa.text("active = true"),
    )

    # ── recording_jobs ─────────────────────────────────────────────────────────
    op.create_table(
        "recording_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("gcs_bucket", sa.Text, nullable=False),
        sa.Column("gcs_object_name", sa.Text, nullable=False),
        sa.Column("gcs_generation", sa.Text, nullable=True),
        sa.Column("file_size", sa.BigInteger, nullable=True),
        sa.Column("extracted_name", sa.Text, nullable=True),
        sa.Column("folder_extension", sa.Text, nullable=True),
        sa.Column("filename_extension", sa.Text, nullable=True),
        sa.Column("phone_number", sa.Text, nullable=True),
        sa.Column("call_timestamp", sa.Text, nullable=True),
        sa.Column("call_id", sa.Text, nullable=True),
        sa.Column("file_extension", sa.Text, nullable=True),
        sa.Column("matched_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("recipient_email", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="received"),
        sa.Column("summary_status", sa.Text, nullable=True),
        sa.Column("assemblyai_transcript_id", sa.Text, nullable=True),
        sa.Column("postmark_message_id", sa.Text, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("emailed_at", sa.DateTime, nullable=True),
    )
    # Prevent duplicate processing of the same GCS object+generation
    op.create_index(
        "unique_gcs_recording",
        "recording_jobs",
        ["gcs_bucket", "gcs_object_name", "gcs_generation"],
        unique=True,
    )
    op.create_index("ix_recording_jobs_status", "recording_jobs", ["status"])
    op.create_index("ix_recording_jobs_created_at", "recording_jobs", ["created_at"])
    op.create_index(
        "ix_recording_jobs_assemblyai_id",
        "recording_jobs",
        ["assemblyai_transcript_id"],
    )

    # ── processing_logs ────────────────────────────────────────────────────────
    op.create_table(
        "processing_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("recording_job_id", UUID(as_uuid=True), sa.ForeignKey("recording_jobs.id"), nullable=True),
        sa.Column("level", sa.Text, nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("metadata_json", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_processing_logs_job_id", "processing_logs", ["recording_job_id"])

    # ── settings ───────────────────────────────────────────────────────────────
    op.create_table(
        "settings",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")),
    )

    # ── admin_users ────────────────────────────────────────────────────────────
    op.create_table(
        "admin_users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("hashed_password", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")),
    )

    # Seed default settings
    op.execute("""
        INSERT INTO settings (key, value) VALUES
        ('debug_mode', 'false'),
        ('store_transcripts', 'false'),
        ('max_retries', '4'),
        ('default_email_subject', 'Call Summary: {name} - {phone} - {date}'),
        ('admin_email', '')
    """)


def downgrade() -> None:
    op.drop_table("admin_users")
    op.drop_table("settings")
    op.drop_table("processing_logs")
    op.drop_index("unique_gcs_recording", table_name="recording_jobs")
    op.drop_index("ix_recording_jobs_status", table_name="recording_jobs")
    op.drop_index("ix_recording_jobs_created_at", table_name="recording_jobs")
    op.drop_index("ix_recording_jobs_assemblyai_id", table_name="recording_jobs")
    op.drop_table("recording_jobs")
    op.drop_index("unique_active_extension", table_name="users")
    op.drop_table("users")
