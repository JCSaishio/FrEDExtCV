#!/bin/bash
#
# start_fred.sh — start the FrED device program inside the fred-venv virtualenv.
#
# Usage (from inside this folder):
#     bash start_fred.sh
#
# It activates fred-venv (creating-by-installer is required first: run
# setup_install.sh once) and launches the main program, main.py.

set -e

# Operate relative to this script's folder.
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
VENV_DIR="$PROJECT_DIR/fred-venv"

if [ ! -d "$VENV_DIR" ]; then
  printf "\nERROR: fred-venv not found.\n"
  printf "Run the installer first:  bash setup_install.sh\n\n"
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

printf "\nRunning FrED application (main.py)...\n"
python main.py
