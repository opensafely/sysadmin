#!/bin/bash
# Install/update all the base packages and groups and system level configuration.
set -euo pipefail

# packages
apt update
apt upgrade -y
cat packages.txt | sed 's/^#.*//' | xargs apt install -y

# ensure groups
for group in developers researchers reviewers; do
    id -g $group 2>&1 > /dev/null || groupadd $group
done
