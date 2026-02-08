# PRD: Slack-Native Organizational Intelligence MVP

## Executive Summary
This MVP validates whether a dedicated Slack onboarding channel can become a reliable source of project truth. The system listens to one channel, **Intern Project** (Slack handle: `#intern-project`), converts each message into a strict graph diff, and applies it to a versioned project knowledge graph with full traceability to the original Slack message.

The immediate value is faster intern onboarding and fewer hidden project conflicts. When a new update introduces a contradiction or dependency cycle, the system sends low-noise notifications only to directly impacted people.

Canonical current truth in MVP is explicitly defined as:
- all active constraints (`is_active = true`) per project, and
- all active dependencies (`is_active = true`) between projects.
This state is queryable directly via read APIs and Neo4j queries.

## Demo Narrative (MVP Validation Scenario)
1. Intern posts a valid constraint update in `#intern-project` using the pinned format.
2. System parses the message, creates a commit, mutates canonical current truth, and posts a thread success reply with `commit_id`.
3. Intern posts a second constraint update for the same project/key but with a different value.
4. System commits the update, detects a constraint conflict, and sends targeted notifications to relevant users.
5. Project owner calls `GET /graph/changes?since=<ISO-8601>` to inspect commit and diff history.
6. Project owner opens `GET /projects/:project_id/checklist` to confirm current active constraints and dependencies.

## Problem Statement
New engineers, especially interns, spend significant time reconstructing context from scattered chats and stale docs. For MVP validation, we need to prove that:
- updates in one Slack channel can be translated into structured graph updates,
- versioned graph history is useful for trust and onboarding,
- automated conflict detection surfaces meaningful issues early.

## User Persona
Primary persona: Incoming software engineering intern at a mid-sized startup (20-150 engineers).
- Posts updates and clarifications in a shared onboarding/project Slack channel.
- Needs a clear, current view of project constraints and dependencies.
- Benefits from seeing where each graph fact came from (Slack permalink).

## User Journey (Core Workflow)
1. Team initializes the MVP with a config file defining project(s), owners, and channel (`Intern Project` / `#intern-project`).
2. Intern posts a project update in the channel.
3. System receives the Slack event and stores raw metadata.
4. System extracts a `ProposedGraphDiff`.
5. If invalid, message is ignored.
6. If valid, diff is committed to graph state with version history.
7. System checks for conflicts.
8. If conflict exists, system notifies only relevant users.
9. User inspects graph state and clicks source permalink for evidence.

## Functional Requirements

### 0) Project Bootstrap from Config
- Before processing Slack updates, system loads an initial config file.
- Config defines:
  - `project_id`
  - `project_name`
  - `owner_user_ids` (one or more Slack user IDs)
  - `slack_channel_name` (`Intern Project`)
  - `slack_channel_id` (preferred for filtering)
- System creates/ensures baseline project and owner links in graph.
- Config supports multiple projects from day one.
- Only configured projects are mutable in MVP.

### 1) Slack Ingestion
- Listen to one configured Slack channel only (`Intern Project`, e.g. `#intern-project`).
- Process each incoming message independently.
- Store message metadata:
  - `message_id`
  - `user_id`
  - `timestamp`
  - `permalink`
  - `raw_text`
- No historical ingestion.
- Even with multiple configured projects, ingestion remains single-channel for MVP.

### 1.1) Required Slack Message Grammar
- Every update message must include `project: <project_id>` as a required token.
- Supported minimal grammar:
  - Constraint upsert:
    - `project: <project_id> constraint: <key>=<value> type: <DesignChoice|Requirement> why: <reason>`
    - `type:` is optional; default is `DesignChoice` when omitted.
  - Dependency add:
    - `project: <project_id> depends_on: <other_project_id> why: <reason>`
- Messages missing `project:` are treated as invalid and ignored.
- Channel pinned template (MVP):
```text
Use one line per update:
1) Constraint:
project: <project_id> constraint: <key>=<value> [type: <DesignChoice|Requirement>] why: <reason> (required)

2) Dependency:
project: <project_id> depends_on: <other_project_id> why: <reason> (required)
```

### 2) Structured Graph Diff Extraction
- For each message, attempt to extract exactly one `ProposedGraphDiff`.
- Supported update types only:
  1. Constraint upsert on a project.
  2. Dependency add (`project_a depends_on project_b`).
- Constraint fields:
  - `project_id`
  - `constraint_key`
  - `constraint_value`
  - `constraint_type` (`DesignChoice` | `Requirement`, optional; defaults to `DesignChoice`)
  - `reason` (required)
- Dependency fields:
  - `from_project_id`
  - `to_project_id`
  - `reason` (required)
- If invalid or unsupported, mark ignored and stop.

### 3) Versioned Graph Update
- On valid diff, create a commit record with:
  - `commit_id`
  - `sequence_number` (global monotonic integer)
  - `timestamp`
  - `actor_user_id`
  - `source_permalink`
  - `diff_json`
  - `commit_message` (short summary including why/reason)
  - `why` (first-class field copied from diff)
  - `parent_commit_id`
- Maintain:
  - canonical current truth: active constraints + active dependencies
  - append-only historical commit log
- Commit topology for MVP: single global linear chain (every commit has exactly one parent except root).
- Implementation stack: Python service with Neo4j.
- Reason propagation semantics:
  - `GraphCommit.why` is copied from `ProposedGraphDiff.reason`.
  - `Constraint.reason` is copied from `ProposedGraphDiff.constraint.reason`.
  - `DEPENDS_ON.reason` is copied from `ProposedGraphDiff.dependency.reason`.
  - `GraphCommit.commit_message` must include the `why` string.

### 3.1) Idempotency and No-Op Rules
- Slack event idempotency key: `message_id`.
  - If already processed, return success with no new commit.
- Constraint no-op:
  - if same project + key + value is already active, do not create a new constraint or commit.
- Dependency no-op:
  - if same active `DEPENDS_ON` edge already exists, do not create a new dependency or commit.
- No-op outcomes are recorded on `SlackMessage.ingestion_status = ignored` with `error_reason = no_op_duplicate`.

### 4) Conflict Detection
- Run after each successfully persisted commit.
- Detect:
  1. Constraint conflict:
     - prior active constraint exists for same `project_id` + `constraint_key`
     - new `constraint_value` differs from prior value
  2. Dependency cycle:
     - new edge creates directed cycle
- On conflict:
  - Create `ConflictReport`.
  - Notify only:
    - author of new update
    - author of conflicting prior constraint (if applicable)
    - configured project owner(s)
- On no conflict: no notification.
- Transaction boundary:
  - Persist commit and graph update first.
  - Run conflict detection second.
  - Conflicts never roll back committed graph updates in MVP.

### 5) Traceability
- Every active constraint and dependency stores:
  - `source_permalink`
  - `author_user_id`
  - `timestamp`
  - `reason`
- Data must be retrievable via minimal API.
- Users can open source Slack message from entity data.

### 6) Minimal Success Feedback
- To avoid silent success, when a valid non-conflicting commit is created:
  - post a lightweight thread reply to the source Slack message:
    - `Committed: <commit_id> | project: <project_id> | summary: <commit_message>`
- Optional fallback for hackathon MVP: periodic digest message (e.g., every 10 commits) in channel.

### 7) Invalid Message Feedback (Conditional)
- Structured attempt threshold (MVP):
  - message contains `project:`, and
  - message contains one of `constraint:` or `depends_on:`.
- Unknown project behavior:
  - if `project:` references a project ID not in config, do not create a commit.
  - post thread reply:
    - `Unknown project_id. Valid projects: <project_id list>`
  - set `SlackMessage.ingestion_status = invalid_unknown_project`.
- If a structured attempt fails schema validation:
  - post a thread reply on the source message:
    - `Could not parse update. Please follow the pinned template.`
- If message is clearly unstructured chatter, ignore silently.
- Feedback is guidance only and does not create a commit.

## Non-Functional Constraints
- Buildable in under 3 hours.
- Single deployable backend service.
- Python runtime.
- Neo4j as only persistence layer.
- Sequential processing (no distributed workers, no orchestration layer).

## Runtime Configuration (MVP)
- Neo4j Aura connection is configured via environment variables:
  - `NEO4J_URI=neo4j+s://f05c537a.databases.neo4j.io`
  - `NEO4J_USERNAME=<your_neo4j_username>`
  - `NEO4J_PASSWORD=<your_neo4j_password>`
  - `NEO4J_DATABASE=neo4j` (default unless changed in Aura)
- Slack and app config remain in `config/projects.json` plus Slack app secrets in env vars.

## Non-Goals
- Multi-channel ingestion.
- Historical Slack backfill.
- Dependency removal/update-delete operations (MVP is add-only for dependencies).
- Jira/GitHub/Notion integration.
- Org hierarchy inference.
- Advanced routing intelligence.
- Enterprise security/compliance layers beyond basic secret management.

## Data Model

### Storage Choice
- Neo4j graph database, accessed from Python (`neo4j` driver).

### Config File (MVP)
`config/projects.json`
```json
{
  "slack": {
    "channel_name": "Intern Project",
    "channel_id": "C12345678"
  },
  "projects": [
    {
      "project_id": "proj-onboarding",
      "name": "Intern Onboarding Service",
      "owner_user_ids": ["U111", "U222"]
    }
  ]
}
```

### Node Labels
- `Project {project_id, name, created_at, updated_at}`
  - `updated_at` is set to the commit timestamp on every successful commit that mutates canonical current truth for that project (`ConstraintUpsert` or `DependencyAdd`).
- `Person {user_id}`
- `Constraint {constraint_id, key, value, type, reason, is_active, source_message_id, source_permalink, author_user_id, created_at}`
- `SlackMessage {message_id, channel_id, user_id, timestamp, permalink, raw_text, ingestion_status, error_reason}`
  - `ingestion_status` values in MVP: `processed | ignored | error | invalid_unknown_project | no_op_duplicate`
- `GraphCommit {commit_id, sequence_number, parent_commit_id, actor_user_id, timestamp, source_message_id, source_permalink, diff_json, why, commit_message}`
- `ConflictReport {conflict_id, conflict_type, details_json, created_at}`

### Relationships
- `(Person)-[:OWNS]->(Project)`
- `(Project)-[:HAS_CONSTRAINT]->(Constraint)`
- `(Project)-[:DEPENDS_ON {dependency_id, reason, is_active, source_message_id, source_permalink, author_user_id, created_at}]->(Project)`
- `(GraphCommit)-[:APPLIES_TO]->(Project)`
- `(GraphCommit)-[:FROM_MESSAGE]->(SlackMessage)`
- `(Constraint)-[:INTRODUCED_BY]->(GraphCommit)`
- `(ConflictReport)-[:TRIGGERED_BY]->(GraphCommit)`

### JSON Schema: `ProposedGraphDiff`
```json
{
  "type": "object",
  "required": ["update_type", "actor_user_id", "source_message_id", "source_permalink", "reason"],
  "properties": {
    "update_type": { "type": "string", "enum": ["ConstraintUpsert", "DependencyAdd"] },
    "actor_user_id": { "type": "string" },
    "source_message_id": { "type": "string" },
    "source_permalink": { "type": "string" },
    "constraint": {
      "type": "object",
      "required": ["project_id", "constraint_key", "constraint_value", "reason"],
      "properties": {
        "project_id": { "type": "string" },
        "constraint_key": { "type": "string" },
        "constraint_value": { "type": "string" },
        "constraint_type": {
          "type": "string",
          "enum": ["DesignChoice", "Requirement"],
          "default": "DesignChoice"
        },
        "reason": { "type": "string", "minLength": 1 }
      }
    },
    "dependency": {
      "type": "object",
      "required": ["from_project_id", "to_project_id", "reason"],
      "properties": {
        "from_project_id": { "type": "string" },
        "to_project_id": { "type": "string" },
        "reason": { "type": "string", "minLength": 1 }
      }
    },
    "reason": { "type": "string", "minLength": 1 }
  },
  "allOf": [
    {
      "if": { "properties": { "update_type": { "const": "ConstraintUpsert" } } },
      "then": { "required": ["constraint"] }
    },
    {
      "if": { "properties": { "update_type": { "const": "DependencyAdd" } } },
      "then": { "required": ["dependency"] }
    }
  ]
}
```

### JSON Shape: `ConflictReport`
```json
{
  "conflict_id": "string",
  "commit_id": "string",
  "conflict_type": "ConstraintConflict | DependencyCycle",
  "details": {
    "project_id": "string",
    "constraint_key": "string",
    "new_value": "string",
    "existing_value": "string",
    "cycle_path": ["project-a", "project-b", "project-a"]
  },
  "notified_user_ids": ["U123", "U456"],
  "created_at": "ISO-8601"
}
```

## API Surface (Minimal)

### Ingestion
- `POST /slack/events`
  - Verify Slack signature.
  - Accept message events for configured channel only.

### Bootstrap/Admin (MVP internal)
- `POST /bootstrap`
  - Load config and seed project + owner nodes.
  - Idempotent.

### Read
- `GET /health`
- `GET /graph/current`
  - Returns canonical current truth only: active constraints + active dependencies.
- `GET /graph/changes?since=<ISO-8601>`
  - Returns commits and entity diffs since timestamp (for “What changed today?”).
- `GET /commits`
- `GET /commits?since=<ISO-8601>`
  - Supports commit-only change feed.
- `GET /conflicts`
- `GET /projects/:project_id`
- `GET /projects/:project_id/checklist`
  - Returns active constraints grouped by type and active dependencies.

### Canonical Current Truth Query (Neo4j)
```cypher
MATCH (p:Project)
OPTIONAL MATCH (p)-[:HAS_CONSTRAINT]->(c:Constraint {is_active: true})
OPTIONAL MATCH (p)-[d:DEPENDS_ON {is_active: true}]->(p2:Project)
RETURN p, collect(DISTINCT c) AS active_constraints, collect(DISTINCT {from: p.project_id, to: p2.project_id, rel: d}) AS active_dependencies;
```

## Slack Event Processing Flow
1. Service starts and loads config from `config/projects.json`.
2. Service seeds/validates baseline project and owner nodes in Neo4j.
3. Receive Slack event at `POST /slack/events`.
4. Verify signature and ignore non-message events.
5. Ignore messages not in configured `channel_id`.
6. Persist `SlackMessage` node.
7. Extract `ProposedGraphDiff`.
8. Validate schema.
9. If invalid: mark message `ignored`; stop.
10. Validate `project_id` against configured projects:
    - if unknown, mark `invalid_unknown_project`, thread-reply with valid project IDs, and stop.
11. Check idempotency/no-op:
    - already-processed `message_id` or duplicate-active update => mark no-op and stop.
12. Create `GraphCommit` node with next global `sequence_number` (single linear parent chain).
13. Apply graph diff and persist commit in one write transaction.
    - `ConstraintUpsert`: deactivate previous active same-key constraint on project, create new active constraint node.
    - `DependencyAdd`: create active `DEPENDS_ON` relationship if absent.
14. Commit transaction.
15. Run conflict detection in a subsequent step.
16. If conflict found: create `ConflictReport` and notify relevant users.
17. If no conflict: send minimal success feedback (thread reply or digest).
18. Mark message `processed`.

## Conflict Detection Logic

### Constraint Conflict
- Condition:
  - a prior active constraint existed for the same `(project_id, key)`, and
  - the new `constraint_value` differs from that prior value.
- Comparison rule:
  - compare against the prior active value captured before deactivation (or preserved in history for deterministic comparison).
- Conflict is detected even though the prior constraint is superseded.
- Output:
  - conflict report with both values and source links.

### Dependency Cycle
- Condition:
  - only traverse `DEPENDS_ON` where `is_active = true`.
  - use DFS or BFS with depth-unbounded traversal (acceptable at MVP scale).
  - after adding `A -> B`, traversal from `B` reaches `A`.
- Output:
  - conflict report including cycle path.

### Minimal Notification Rule
- Notify unique set of:
  - new commit actor,
  - prior conflicting constraint author (if applicable),
  - configured owner(s) of involved project.
- No conflict means no notification.

### Commit and Conflict Semantics
- Commit acceptance is independent from conflict status.
- Conflict is an annotation on committed truth, not a rejection path.

## Success Metrics for MVP Validation
- `% of channel messages parsed into valid diffs`.
- `# of graph commits/week from Intern Project channel`.
- `% of valid messages resulting in visible success feedback`.
- `% of conflicts confirmed as legitimate by users`.
- `# of times source Slack permalinks are opened from graph views (if measurable)`.
- Qualitative onboarding feedback on clarity of canonical current truth.

## Risks & Simplifications
- Risk: free-form Slack text lowers parse reliability.
  - Simplification: pin a lightweight message template in `#intern-project`.
- Risk: over-modeling graph history in MVP.
  - Simplification: only support two update types and one commit chain.
- Risk: notification fatigue.
  - Simplification: notify only direct actors + configured owners on conflicts.
- Risk: silent success reduces trust.
  - Simplification: always provide minimal success acknowledgment for non-conflicting commits.
- Risk: bootstrapping drift.
  - Simplification: config is source of truth for initial projects/owners.

## Neo4j Constraints and Indexes (MVP)
```cypher
CREATE CONSTRAINT project_project_id_unique IF NOT EXISTS
FOR (p:Project) REQUIRE p.project_id IS UNIQUE;

CREATE CONSTRAINT person_user_id_unique IF NOT EXISTS
FOR (u:Person) REQUIRE u.user_id IS UNIQUE;

CREATE CONSTRAINT slack_message_id_unique IF NOT EXISTS
FOR (m:SlackMessage) REQUIRE m.message_id IS UNIQUE;

CREATE CONSTRAINT graph_commit_id_unique IF NOT EXISTS
FOR (c:GraphCommit) REQUIRE c.commit_id IS UNIQUE;

CREATE CONSTRAINT graph_commit_sequence_unique IF NOT EXISTS
FOR (c:GraphCommit) REQUIRE c.sequence_number IS UNIQUE;

CREATE CONSTRAINT constraint_constraint_id_unique IF NOT EXISTS
FOR (c:Constraint) REQUIRE c.constraint_id IS UNIQUE;

CREATE CONSTRAINT conflict_report_id_unique IF NOT EXISTS
FOR (r:ConflictReport) REQUIRE r.conflict_id IS UNIQUE;

CREATE INDEX constraint_active_lookup IF NOT EXISTS
FOR (c:Constraint) ON (c.is_active, c.key);

CREATE INDEX constraint_project_key_active IF NOT EXISTS
FOR (c:Constraint) ON (c.project_id, c.key, c.is_active);

CREATE INDEX commit_timestamp_lookup IF NOT EXISTS
FOR (c:GraphCommit) ON (c.timestamp);
```
The `constraint_project_key_active` index supports fast lookups for constraint conflict detection.

## Open Questions
- None for current MVP scope.
