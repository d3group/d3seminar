#!/bin/zsh
cd -- "$(dirname "$0")/.." || exit 1
python3 scripts/seminar.py test-mail invite
printf '\nPress Enter to close this window.'
read -r
