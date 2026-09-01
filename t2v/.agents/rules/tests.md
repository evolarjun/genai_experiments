# Integration Test Rules

When running integration tests for `t2v` (`perl t/integration.t` or `prove`), always set `BypassSandbox: true` so `tmux` can access socket files under `/private/tmp`.
