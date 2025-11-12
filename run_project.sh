#!/bin/bash
# Quick script to run project 
# Usage: ./run_project.sh <project name>

cd "$(dirname "$0")"
source .venv/bin/activate
python src/main.py --targets $1
