#!/bin/bash
#
# check_stepper.sh — skipped-step check of the extrusion stepper, in the
# terminal (step_check.py). Close main.py first: both drive the same pins.
#
# Usage (from inside this folder):
#     bash check_stepper.sh                          # sweep 1, 2, 5, 10, 15, 20 RPM
#     bash check_stepper.sh --speeds 1.5 3 --revs 2  # own speeds / revolutions
#     bash check_stepper.sh --help                   # every option

set -e

# Operate relative to this script's folder.
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
VENV_DIR="$PROJECT_DIR/fred-venv"

# Same venv lookup as start_fred.sh (the venv may still be in the old v6
# folder after the v7 rename).
OLD_VENV_DIR="$(dirname "$PROJECT_DIR")/fred-device-extcv-pi4v6/fred-venv"
if [ ! -d "$VENV_DIR" ] && [ -d "$OLD_VENV_DIR" ]; then
  VENV_DIR="$OLD_VENV_DIR"
fi

if [ -d "$VENV_DIR" ]; then
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  PYTHON=python
else
  # The check only needs RPi.GPIO, which the installer takes from apt, so the
  # system Python works too.
  printf "\nfred-venv not found - using the system python3.\n"
  PYTHON=python3
fi

exec "$PYTHON" step_check.py "$@"
