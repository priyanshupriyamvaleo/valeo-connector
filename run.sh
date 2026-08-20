#!/usr/bin/env bash
# Start the Valeo connector. No dependencies, no virtualenv needed.
cd "$(dirname "$0")"
exec python3 server.py
