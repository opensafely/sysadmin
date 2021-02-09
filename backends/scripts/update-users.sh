#!/bin/bash
set -euo pipefail

# files containing lists of gh user names for the different roles
# note: probably will be API calls to job-server in future
developers=$1
researchers=${2:-}
reviewers=${3:-}

# add a user, their gh ssh keys, and add to groups
add_user() {
    local user=$1
    shift
    local groups="$@"
    if id -u $user 2>&1 >/dev/null; then
        echo "User $user already exists"
    else
        useradd $user
    fi

    for group in $groups; do
        usermod -a -G $group $user
    done
    echo "User in groups: $groups"

    mkdir -p /home/$user/.ssh
    local keysfile=/home/$user/.ssh/authorized_keys
    touch $keysfile

    echo "Updating Github public keys for user"
    ssh-import-id gh:$user --output $keysfile
    # add explicit keys from this repo
    if test -f keys/$user; then
         cat keys/$user >> $keysfile
    fi
    
    # dedupe keysfile
    local tmp=$(mktemp)
    cat $keysfile | sort | uniq > $tmp
    mv $tmp $keysfile
}

# add a list of users from a file, to specific groups
add_group() {
    local file=$1
    shift
    local groups="$@"
    while read -r user; do
        if test "${user::1}" = "#"; then  # skip comments
            continue
        elif test -z "$user"; then  # skip blank lines
            continue
        else
            add_user $user $groups
        fi
    done < $file
}

# developers is mandatory
add_group $developers developers researchers reviewers
# other groups depend on backend
test -f $researchers && add_group $researchers researchers reviewers
test -f $reviewers && add_group $reviewers reviewers
