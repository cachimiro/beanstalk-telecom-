"""email pipeline overhaul — rename postmark columns, add speaker classification and dual email columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename postmark_message_id → email_message_id
    op.alter_column("recording_jobs", "postmark_message_id", new_column_name="email_message_id")
    # Add transcript email message ID (replaces postmark_transcript_message_id)
    op.add_column("recording_jobs", sa.Column("email_transcript_message_id", sa.Text, nullable=True))
    # Speaker re-classification outputs
    op.add_column("recording_jobs", sa.Column("speaker_confidence_score", sa.Float, nullable=True))
    op.add_column("recording_jobs", sa.Column("speaker_classification_reason", sa.Text, nullable=True))
    # Detected language from subject-line call
    op.add_column("recording_jobs", sa.Column("detected_language", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("recording_jobs", "detected_language")
    op.drop_column("recording_jobs", "speaker_classification_reason")
    op.drop_column("recording_jobs", "speaker_confidence_score")
    op.drop_column("recording_jobs", "email_transcript_message_id")
    op.alter_column("recording_jobs", "email_message_id", new_column_name="postmark_message_id")
