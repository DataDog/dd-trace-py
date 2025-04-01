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
    uninstaller.py uninstaller.sh introduction/templates/Lab/A9/a9_lab2.html

# a9_lab2, which we don't use, requires Pillow which requires the jpg lib 
# and CI doesn't have it, so we nuke it from existence
sed -i '/^Pillow==9.4.0$/d' requirements.txt
sed -i '/^from PIL import Image *,* *ImageMath *$/d' introduction/views.py
sed -i '' '
/^@csrf_exempt$/{
  N
  /^@csrf_exempt\ndef a9_lab2(request):$/{
    :a
    N
    /\n\n@authentication_decorator$/!ba
    /\n@authentication_decorator$/!ba
    s/.*\n@authentication_decorator/@authentication_decorator/
  }
}' views.py
sed -i '/^.*a9_lab2/d' introduction/urls.py

cd ..
XZ_OPT=-9 tar -cJf pygoat.xz pygoat/
rm -rf pygoat/
