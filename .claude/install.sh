#!/bin/bash

SCRIPT_PATH="$(realpath "${BASH_SOURCE}")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

cp -R $SCRIPT_DIR/hooks ~/.claude