#!/bin/bash
exec python3 "$(dirname "$(realpath "${BASH_SOURCE[0]}")")/install.py" "$@"
