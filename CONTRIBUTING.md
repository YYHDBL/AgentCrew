# Contributing to AgentCrew

AgentCrew combines a local Python task service with an Electron desktop client. Changes should preserve persistent execution records, approval boundaries, recovery behavior, and the isolation of workspace data.

## Report an issue

Use the repository's Issues page. Include your macOS version, processor architecture, application or commit version, the steps that reproduce the problem, and the expected and observed behavior. Include relevant error messages or a small example file when they help reproduce the issue. Remove API keys, access tokens, private task materials, and personal data from attachments.

## Prepare a change

Start from `main` and install dependencies using the lockfiles. Follow the [development guide](docs/development.md) for commands and directory conventions. Keep each pull request focused on a concrete problem and explain the resulting behavior and the checks you ran.

For HTTP or event changes, update the contracts in `docs/contracts/`, regenerate the desktop types and validator, and update affected callers and tests together. Generated files are committed so a normal desktop build can use them directly.

Tests that exercise persistence, process lifecycle, files, or external integrations should use the actual components involved. Preserve real regression captures and verify failure conditions as well as successful execution. Tests that call a model require your own credentials and may incur provider charges.

## Keep local data private

Runtime data, credentials, dependency directories, application bundles, and test output belong in the ignored directories declared by `.gitignore`. Internal design notes, implementation plans, and acceptance output also remain local. Commit source, lockfiles, contracts, public documentation, and the test inputs required to reproduce a regression.

Review `git diff --cached` before submitting a change. For database or recovery changes, describe how existing user data is preserved and which actual migration or restart scenario you verified.
