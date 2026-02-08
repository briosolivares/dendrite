from neo4j import Driver, GraphDatabase
from neo4j.exceptions import Neo4jError

from app.config import get_settings


def get_driver() -> Driver:
    settings = get_settings()
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )


def verify_driver_connectivity(driver: Driver) -> None:
    driver.verify_connectivity()


def check_database_health(driver: Driver) -> tuple[bool, str]:
    settings = get_settings()
    try:
        with driver.session(database=settings.neo4j_database) as session:
            session.run("RETURN 1 AS ok").single()
        return True, "ok"
    except (Neo4jError, OSError, RuntimeError) as exc:
        return False, str(exc)
