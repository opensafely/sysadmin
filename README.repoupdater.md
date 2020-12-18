# RepoUpdater

This repo contains a script that lets you apply changes to all OpenSAFELY
research repos at once, and then create a pull request for each repo.

## Status

This script has been used once!

## Setup

* Set up a virtual environment and install requirements from requirements.txt
* Create a GitHub personal access token, and write it in a file called token.txt

## Usage example

```
# Clone all research repos into research/, or pull if already cloned
$ python repoupdater.py update

# In each repo, check out a new branch
# Note that -- is required to stop argparse treating -b as argument to repoupdater.py
$ python repoupdater.py exec -- git checkout -b fix-suppression-codelists

# Update codelists.txt (could just use sed)
$ python repoupdater.py exec -- sed -i s/suppresion/suppression/ codelists/codelists.txt

# Update study definitions (need to use sed, for wildcard support)
$ sed -i s/suppresion/suppression/ research/*/analysis/*.py

# Add, commit, push
# Note that if nothing has been added, the commit will error -- ignore this
$ python repoupdater.py exec git add .
$ python repoupdater.py exec -- git commit -m "Fix typo in codelist name"
$ python repoupdater.py exec -- git push -u origin fix-suppression-codelists

# Create PRs
# Note that errors will be reported for repos where nothing has changed
$ python repoupdater.py pull-request fix-suppression-codelists "Fix typo in codelist name"
```
