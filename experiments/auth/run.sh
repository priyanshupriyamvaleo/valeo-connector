#!/usr/bin/env bash
# Run the whole auth experiment: start the three servers, run the client, tear everything
# down. Nothing is left running and nothing is installed.
#
#   ./run.sh              automated, start to finish
#   ./run.sh --manual     pauses so you can sign in through the browser yourself

set -u
cd "$(dirname "$0")"

LOGS=$(mktemp -d)
PIDS=()

cleanup() {
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null
  done
  wait 2>/dev/null
}
trap cleanup EXIT INT TERM

start() {           # start <name> <script> <port>
  python3 "$2" >"$LOGS/$1.log" 2>&1 &
  PIDS+=("$!")
  for _ in $(seq 1 50); do
    if curl -sf -o /dev/null "http://127.0.0.1:$3/health" 2>/dev/null \
       || curl -sf -o /dev/null "http://127.0.0.1:$3/.well-known/jwks.json" 2>/dev/null; then
      printf '  %-12s http://127.0.0.1:%s\n' "$1" "$3"
      return 0
    fi
    sleep 0.2
  done
  echo "  $1 failed to start:" && cat "$LOGS/$1.log" && return 1
}

echo "Starting the pieces"
start auth       auth_server.py 9002 || exit 1
start valeo-api  valeo_api.py   9001 || exit 1
start connector  mcp_server.py  8788 || exit 1

echo
python3 test_client.py "$@"
STATUS=$?

echo "Server logs: $LOGS"
exit $STATUS
