#!/usr/bin/env bash
# serena/run-server.sh
#
# Wrapper that starts the Serena MCP server with its native streamable-http
# transport, optionally pre-activating a project.
#
# Env vars:
#   SERENA_ACTIVE_PROJECT  optional project to activate at startup. Either a
#                          bare directory name under /workspaces/projects
#                          (e.g. "my-repo") or a full in-container path
#                          (e.g. "/workspaces/projects/my-repo").
#                          When unset, agents activate a project at runtime
#                          via the activate_project tool.
set -eu

args=(start-mcp-server --transport streamable-http --port 9121 --host 0.0.0.0)

if [ -n "${SERENA_ACTIVE_PROJECT:-}" ]; then
  case "$SERENA_ACTIVE_PROJECT" in
    /*) project="$SERENA_ACTIVE_PROJECT" ;;
    *)  project="/workspaces/projects/$SERENA_ACTIVE_PROJECT" ;;
  esac
  echo "[run-server] activating project: $project"
  args+=(--project "$project")
fi

exec serena "${args[@]}"
