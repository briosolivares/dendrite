from neo4j import Driver

from app.config import get_settings, load_projects_config
from app.models import BootstrapResponse, ParsedMessage, SlackEvent
from app.parser import parse_event


def process_slack_event(event: SlackEvent) -> ParsedMessage:
    return parse_event(event)


def bootstrap_from_config(driver: Driver) -> BootstrapResponse:
    projects_config = load_projects_config()
    project_payload = [
        {
            "project_id": project.project_id,
            "name": project.name,
            "owner_user_ids": project.owner_user_ids,
        }
        for project in projects_config.projects
    ]

    owner_link_count = sum(len(project["owner_user_ids"]) for project in project_payload)

    query = """
    UNWIND $projects AS project
    MERGE (p:Project {project_id: project.project_id})
      ON CREATE SET p.created_at = datetime().epochMillis
    SET p.name = project.name, p.updated_at = datetime().epochMillis
    WITH p, project
    UNWIND project.owner_user_ids AS owner_user_id
    MERGE (person:Person {user_id: owner_user_id})
    MERGE (person)-[:OWNS]->(p)
    """

    settings = get_settings()
    with driver.session(database=settings.neo4j_database) as session:
        session.run(query, projects=project_payload).consume()

    return BootstrapResponse(
        ok=True,
        detail="Bootstrap complete. Project/owner graph is synchronized from config.",
        project_count=len(project_payload),
        owner_link_count=owner_link_count,
    )
