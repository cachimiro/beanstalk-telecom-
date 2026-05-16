#!/usr/bin/env python3
"""Create the initial admin user. Run once after first deployment.

Usage:
  python scripts/create_admin.py admin@example.com yourpassword
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.config import settings
from api.models.admin_user import AdminUser
from api.auth import hash_password

engine = create_engine(settings.DATABASE_URL)
Session = sessionmaker(bind=engine)


def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/create_admin.py <email> <password>")
        sys.exit(1)

    email = sys.argv[1]
    password = sys.argv[2]

    session = Session()
    existing = session.query(AdminUser).filter_by(email=email).first()
    if existing:
        print(f"Admin user {email} already exists.")
        session.close()
        return

    admin = AdminUser(email=email, hashed_password=hash_password(password))
    session.add(admin)
    session.commit()
    session.close()
    print(f"Admin user created: {email}")


if __name__ == "__main__":
    main()
