# 2-Hour MVP Build Prompts (Sequential)

Use these prompts in order. Each is intentionally small and focused so the result is a working vertical slice aligned with the PRD.

## 1) Project skeleton
Create a minimal Python backend with FastAPI.
- Add structure: `app/main.py`, `app/config.py`, `app/neo4j_client.py`, `app/models.py`, `app/parser.py`, `app/service.py`, `app/conflicts.py`, `app/routes/slack.py`, `app/routes/read.py`, `app/routes/bootstrap.py`.
- Add `requirements.txt` and `.env.example`.
- Ensure the app starts successfully.

## 2) Runtime config + project config loader
Implement config loading for env vars and `config/projects.json`.
- Env vars: `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`.
- Validate `projects.json` shape: one Slack channel, multiple projects, required fields.
- Fail fast with clear startup errors if config is invalid.

## 3) Neo4j client and health check
Implement Neo4j driver lifecycle and health endpoint.
- Connect on startup, close on shutdown.
- Add `GET /health` returning app + database status.

## 4) Minimal graph constraints/indexes
Add an idempotent migration helper for critical Neo4j schema only.
- Unique constraints for `Project.project_id`, `SlackMessage.message_id`, `GraphCommit.commit_id`, `GraphCommit.sequence_number`.
- Index: `constraint_project_key_active` on `(project_id, key, is_active)`.

## 5) Bootstrap endpoint
Implement `POST /bootstrap`.
- Seed `Project` nodes from config.
- Seed `Person` nodes and `(Person)-[:OWNS]->(Project)`.
- Make endpoint idempotent.

## 6) Slack URL verification + request signature verification
Implement Slack request authenticity handling for `POST /slack/events`.
- Handle Slack `url_verification` payload by returning the `challenge` value.
- Verify `X-Slack-Signature` and `X-Slack-Request-Timestamp` using `SLACK_SIGNING_SECRET`.
- Reject requests with timestamp older than 5 minutes.

## 7) Slack event filtering + permalink resolution
Add ingestion pre-processing for Slack events.
- Ignore bot messages and any event with `subtype` set.
- Persist `event_id` (when present), `ts`, `channel_id`, `user_id`, `raw_text`.
- Add helper to resolve `source_permalink` via Slack Web API `chat.getPermalink` using `SLACK_BOT_TOKEN`, with safe fallback if API call fails.
- Add optional unique constraint for `SlackMessage.event_id` if feasible.

## 8) Slack events ingress (single channel)
Implement `POST /slack/events` message ingestion behavior.
- Accept message events only.
- Filter to configured `channel_id` only.
- Explicitly ignore bot replies to avoid loops.
- Persist a `SlackMessage` node with raw metadata, including `event_id` when available.

## 9) Structured attempt detection and feedback stubs
Add structured-attempt logic.
- Structured attempt = contains `project:` and one of `constraint:` or `depends_on:`.
- Add helper for thread feedback (real Slack call or log-based stub).

## 10) Strict parser for constraint updates
Implement parser for:
- `project: <project_id> constraint: <key>=<value> [type: <DesignChoice|Requirement>] why: <reason>`
- `type` defaults to `DesignChoice`.
- Output `ProposedGraphDiff` with required fields.

## 11) Strict parser for dependency updates
Implement parser for:
- `project: <project_id> depends_on: <other_project_id> why: <reason>`
- Map to `DependencyAdd` with `from_project_id = project`.

## 12) Diff validation and invalid-format handling
Validate parsed diff.
- If schema-invalid and structured attempt: thread reply `Could not parse update. Please follow the pinned template.`
- Mark SlackMessage status as `ignored`.
- Do not create commit.

## 13) Unknown project guard
Before commit creation, validate project IDs exist in config.
- On unknown project: do not commit.
- Set status `invalid_unknown_project`.
- Thread reply: `Unknown project_id. Valid projects: <list>`.

## 14) Idempotency + no-op checks
Implement no-op/idempotency guards.
- If message already processed: stop.
- Constraint no-op: same project/key/value active -> stop.
- Dependency no-op: same active edge exists -> stop.
- Mark status `no_op_duplicate` with reason.

## 15) Global linear commit creation
Implement commit creation.
- Fields: `commit_id`, `sequence_number`, `parent_commit_id`, `actor_user_id`, `timestamp`, `source`, `diff_json`, `why`, `commit_message`.
- Enforce single global linear chain by always linking to latest commit.
- Prevent `sequence_number` race conditions with a single global `asyncio.Lock` around the “create commit + apply diff” path.
- Acceptable alternative: retry once on Neo4j unique constraint violation of `sequence_number`.

## 16) Apply diff transaction (mutate truth)
In one write transaction:
- Create commit node.
- Apply `ConstraintUpsert`:
  - capture prior active value,
  - deactivate prior active same key,
  - insert new active constraint with reason + traceability.
- Apply `DependencyAdd`:
  - create active `DEPENDS_ON` with reason + traceability.
- Update `Project.updated_at` to commit timestamp for mutated projects.

## 17) Conflict detection after commit
Run conflict detection after commit transaction.
- Constraint conflict: prior active same `(project_id, key)` existed and value differs.
- Dependency cycle: traverse active `DEPENDS_ON` only; cycle if adding `A->B` makes `B` reach `A`.
- Conflicts do not roll back commit.

## 18) Conflict report + targeted notification
If conflict exists:
- Persist `ConflictReport`.
- Notify only relevant users (new actor, prior conflicting author if applicable, project owners).
- If Slack notification integration is not ready, log a clearly structured payload.

## 19) Success feedback on non-conflicting commits
For valid commits with no conflict:
- Send minimal success thread reply:
  - `Committed: <commit_id> | project: <project_id> | summary: <commit_message>`

## 20) Read API: current truth
Implement `GET /graph/current`.
- Return only canonical current truth:
  - active constraints,
  - active dependencies,
  - with permalink/author/timestamp/reason.

## 21) Read API: changes feed
Implement `GET /graph/changes?since=<ISO-8601>`.
- Return commits since timestamp.
- For MVP entity diffs, return each commit’s stored `diff_json` (no expensive graph diff computation).

## 22) Read API: project + checklist
Implement:
- `GET /projects/:project_id`
- `GET /projects/:project_id/checklist`
Checklist returns:
- active constraints grouped by type,
- active dependencies.

## 23) Demo seed script
Add a script or curl collection that demonstrates:
- valid constraint commit + success reply,
- conflicting constraint commit + conflict report,
- query `/graph/changes?since=...`,
- query project checklist.

## 24) Final polish for demo reliability
Do a quick pass to ensure:
- ingestion statuses are consistent (`processed`, `ignored`, `error`, `invalid_unknown_project`, `no_op_duplicate`),
- reason propagation is correct,
- endpoints return predictable JSON,
- logs clearly show commit and conflict outcomes.
