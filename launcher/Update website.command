#!/bin/bash
# Double-click on a Mac to open the website control panel.
cd "$(dirname "$0")"
python3 start.py
read -n 1 -s -r -p "Press any key to close this window."
