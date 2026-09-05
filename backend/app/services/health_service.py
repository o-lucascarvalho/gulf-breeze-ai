from sqlalchemy import text
from sqlalchemy.engine import Engine


def check_database_health(engine: Engine) -> tuple[bool, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:  # pragma: no cover
        return False, str(exc)
