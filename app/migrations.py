CRITICAL_SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE CONSTRAINT project_project_id_unique IF NOT EXISTS "
    "FOR (p:Project) REQUIRE p.project_id IS UNIQUE",
    "CREATE CONSTRAINT slack_message_message_id_unique IF NOT EXISTS "
    "FOR (m:SlackMessage) REQUIRE m.message_id IS UNIQUE",
    "CREATE CONSTRAINT slack_message_event_id_unique IF NOT EXISTS "
    "FOR (m:SlackMessage) REQUIRE m.event_id IS UNIQUE",
    "CREATE CONSTRAINT graph_commit_commit_id_unique IF NOT EXISTS "
    "FOR (gc:GraphCommit) REQUIRE gc.commit_id IS UNIQUE",
    "CREATE CONSTRAINT graph_commit_sequence_number_unique IF NOT EXISTS "
    "FOR (gc:GraphCommit) REQUIRE gc.sequence_number IS UNIQUE",
    "CREATE INDEX constraint_project_key_active IF NOT EXISTS "
    "FOR (c:Constraint) ON (c.project_id, c.key, c.is_active)",
)
