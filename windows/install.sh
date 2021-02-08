#/bin/bash
set -eu
apt update
apt upgrade -y
cat packages.txt | sed 's/^#.*//' | xargs apt install -y

