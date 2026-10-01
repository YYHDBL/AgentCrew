# AgentCrew

AgentCrew is a local-first Agent Harness for long-running worker agents, coding agents, and productivity workflows. It grew from MyCodeAgent, an experimental local coding agent, into a desktop runtime for work that needs controlled tool use, human approvals, queued instructions, and recovery after interruptions.

The application combines a Python backend with an Electron and React desktop client. Execution events provide an auditable record of tool calls, approvals, results, and recovery. The product is designed for one local user on macOS; scheduled work requires the application and computer to remain available.

## Run locally

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js, and npm.

Install the backend dependencies:

```sh
cd backend
uv sync
```

Install the desktop dependencies and start the application:

```sh
cd desktop
npm ci
npm run dev
```

## Verify

Run the backend tests:

```sh
cd backend
uv run pytest -q
```

Type-check and build the desktop application:

```sh
cd desktop
npm run build
```

## Documentation

- [Product definition](docs/product/PRODUCT.md) and [user flows](docs/product/USER-FLOWS.md)
- [Architecture](docs/architecture/architecture.md), [API contract](docs/contracts/openapi.yaml), and [event contract](docs/contracts/events.md)
- [Acceptance evidence](docs/acceptance/) and [documentation map](docs/README.md)

The product specifications describe the intended behavior; check the acceptance records for the functionality verified in the current code.
