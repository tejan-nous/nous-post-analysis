#!/bin/bash
cd "$(dirname "$0")"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}"
python3 server.py
