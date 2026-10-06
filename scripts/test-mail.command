#!/bin/zsh
cd -- "$(dirname "$0")/.." || exit 1
python3 scripts/mailer.py invite
printf '\nPress Enter to close this window.'
read -r
