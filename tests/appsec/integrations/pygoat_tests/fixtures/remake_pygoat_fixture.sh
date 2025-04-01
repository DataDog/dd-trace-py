#!/bin/sh
# Run this script to update the pygoat compressed tarball

set -e 
VERSION=v2.0.1

rm -f pygoat.xc
rm -rf pygoat/
git clone --depth 1 --branch $VERSION https://github.com/adeyosemanputra/pygoat.git
cd pygoat
rm -rf chatbot docker-compose.yml Dockerfile docs gh-md-toc installer.sh .git .github \
    app.log Procfile PyGoatBot.py README.md runtime.txt test.log temp.py \
    uninstaller.py uninstaller.sh
cd ..
XZ_OPT=-9 tar -cJf pygoat.xz pygoat/
rm -rf pygoat/
