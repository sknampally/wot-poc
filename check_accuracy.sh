#!/bin/bash
# Quick script to run accuracy check
# Usage: ./check_accuracy.sh

cd "$(dirname "$0")"
source .venv/bin/activate
python src/main.py --check-accuracy

