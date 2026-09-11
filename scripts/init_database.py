from src.database import DATABASE_PATH, db


def initialize_database() -> None:
    print("Initializing the finance demo database...")
    print(f"Database location: {DATABASE_PATH}")

    db.create_tables_sync()

    print("Database initialization completed successfully.")


if __name__ == "__main__":
    initialize_database()