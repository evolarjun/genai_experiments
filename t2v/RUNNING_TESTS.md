# Running Tests in t2v

This document explains how to run unit and integration tests for `t2v`, including sandbox requirements for terminal integration tests.

## Test Suite Structure

The test suite consists of two test files under `t/`:

1. `t/unit.t` (Unit Tests)
   - Evaluates pure functions, calculations, cell formatting, search, and row filtering logic.
   - Loads `t2v` directly into the Perl process (`do "t2v"`).
   - Runs fast without requiring external subprocesses or terminal emulators.

2. `t/integration.t` (Integration Tests)
   - Evaluates interactive TUI behavior (scrolling, keybindings, prompts, help screen).
   - Launches `t2v` inside background `tmux` sessions to inspect terminal output and test key input sequences.

## Running Tests

To save time run the unit test first because it's fast and fix any bugs
identified by that test before running the integration test.

### Running Unit Tests (Standard Environment / Sandboxed)
Unit tests can run in any standard shell or sandboxed environment

```sh
perl t/unit.t
```

### Running Integration Tests (Sandbox Bypass Requirement)
Integration tests require `tmux` to simulate terminal window state.

```sh
perl t/integration.t
```

#### Why Sandbox Bypass is Needed for AI Assistants
When running inside isolated agent sandboxes:
- `tmux` creates UNIX domain sockets under `/private/tmp/tmux-<UID>/default`.
- Standard sandbox isolation (`BypassSandbox: false`) restricts filesystem access outside the workspace, causing `tmux` commands to fail with:
  `error connecting to /private/tmp/tmux-... (Operation not permitted)`
- Setting `BypassSandbox: true` on the execution tool grants `tmux` the required access to `/private/tmp` so integration tests can run.

