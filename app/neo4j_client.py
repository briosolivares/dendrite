from neo4j import Driver, GraphDatabase
from neo4j.exceptions import Neo4jError

from app.config import get_settings
from app.migrations import CRITICAL_SCHEMA_STATEMENTS


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


def run_critical_schema_migrations(driver: Driver) -> None:
    settings = get_settings()
    with driver.session(database=settings.neo4j_database) as session:
        for statement in CRITICAL_SCHEMA_STATEMENTS:
            session.run(statement).consume()
