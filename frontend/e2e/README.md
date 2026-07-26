# e2e tests

These run against a real, fully running stack — not mocks, not an ephemeral
testcontainer stack like the backend's pytest suite. They exist to catch the class
of bug unit tests can't (wrong API paths, cookie/CORS issues, the SSE stream
actually working end to end), the same way the backend milestones were each
verified by hand against live infra before committing.

As of this chunk, these run automatically in CI on every push and pull request
(see the `e2e` job in `.github/workflows/ci.yml`) using the built Docker images,
not `npm run dev`. The manual setup below is for local iteration.

## Setup

```bash
# 1. Bring up the backend stack
docker compose -f ../infra/docker-compose.yml up -d postgres redis neo4j backend worker

# 2. Migrate + seed
docker compose -f ../infra/docker-compose.yml exec backend \
  python -m alembic -c /workspace/backend/alembic.ini upgrade head
docker compose -f ../infra/docker-compose.yml exec backend \
  flowsage-backend seed-personas
docker compose -f ../infra/docker-compose.yml exec backend \
  flowsage-backend create-user e2e@flowsage.dev supersecret123

# 3. Start the frontend
npm run dev

# 4. Run the tests
npx playwright test
```

`ANTHROPIC_API_KEY` doesn't need to be set for these tests to pass — the
Predictive Engine flow only asserts that a run was created and reaches a terminal
state (completed or failed), not that it succeeds, since a real vision call needs
a real key.
