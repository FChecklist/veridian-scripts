#!/bin/bash
set -a
source /opt/veridian/shared/.env
set +a
unset ANTHROPIC_API_KEY
unset ANTHROPIC_API_KEY_UNCONFIRMED
unset CLAUDE_CODE_OAUTH_TOKEN
cd /opt/veridian/workspace
exec $HOME/.local/bin/claude
