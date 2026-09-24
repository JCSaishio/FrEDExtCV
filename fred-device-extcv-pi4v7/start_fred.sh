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

# The folder was renamed from fred-device-extcv-pi4v6 in v7. `git pull` moves
# the code but leaves the (git-ignored) fred-venv in the old folder, and a venv
# cannot simply be moved (its paths are fixed at creation). Keep using it from
# there, so updating needs no reinstall.
OLD_VENV_DIR="$(dirname "$PROJECT_DIR")/fred-device-extcv-pi4v6/fred-venv"
if [ ! -d "$VENV_DIR" ] && [ -d "$OLD_VENV_DIR" ]; then
  printf "\nUsing the existing fred-venv from the old v6 folder:\n  %s\n" "$OLD_VENV_DIR"
  VENV_DIR="$OLD_VENV_DIR"
fi

if [ ! -d "$VENV_DIR" ]; then
  printf "\nERROR: fred-venv not found.\n"
  printf "Run the installer first:  bash setup_install.sh\n\n"
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

printf "\nRunning FrED application (main.py)...\n"
python main.py
