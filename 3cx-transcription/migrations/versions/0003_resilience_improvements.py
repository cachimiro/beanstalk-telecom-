"""resilience improvements — dedup constraint, stored email HTML, email_failed status

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    # Store generated HTML so email_failed jobs can be retried without
    # re-running the full pipeline.
    op.add_column("recording_jobs", sa.Column("summary_html", sa.Text(), nullable=True))
    op.add_column("recording_jobs", sa.Column("transcript_html", sa.Text(), nullable=True))

    # Prevent duplicate jobs from rapid Pub/Sub retries.
    # Partial index: only enforce uniqueness when generation is not null,
    # since test pipeline jobs have no generation value.
    op.create_index(
        "uq_recording_jobs_object_generation",
        "recording_jobs",
        ["gcs_object_name", "gcs_generation"],
        unique=True,
        postgresql_where=sa.text("gcs_generation IS NOT NULL"),
    )


def downgrade():
    op.drop_index("uq_recording_jobs_object_generation", table_name="recording_jobs")
    op.drop_column("recording_jobs", "transcript_html")
    op.drop_column("recording_jobs", "summary_html")
