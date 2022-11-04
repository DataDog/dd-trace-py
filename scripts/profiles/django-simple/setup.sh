#!/usr/bin/env bash -eu

set -eu

PREFIX=${1}
AUSTIN_VERSION="3.4.1"

export DJANGO_SETTINGS_MODULE="config.settings.production"
export DJANGO_ALLOWED_HOSTS="127.0.0.1"
export DJANGO_SECRET_KEY="SECRET_KEY"
export DATABASE_URL="sqlite:///django.db"

# Clean up existing installation
test -d $PREFIX && rm -rf $PREFIX || mkdir -p $PREFIX

if [[ "$OSTYPE" != "linux-gnu"* && "$OSTYPE" != "darwin"* ]]
then
    echo "Platform $OSTYPE not supported."
    exit 1
fi

# Create and activate the virtualenv
python3.8 -m venv ${PREFIX}
source ${PREFIX}/bin/activate
pip install pip --upgrade

# Install the application
git clone https://github.com/DataDog/trace-examples.git ${PREFIX}/trace-examples
pushd ${PREFIX}/trace-examples/
    git checkout 760deae1fd2f2371cf813d3ff3ca9f0e040e8c60
    pushd python/django/django-simple
        pip install -r requirements/production.txt
        python manage.py migrate
    popd
popd

# Install k6 and austin
pushd ${PREFIX}
    if [[ "$OSTYPE" == "linux-gnu"* ]]
    then
        curl -s https://github.com/p403n1x87/austin/releases/download/v${AUSTIN_VERSION}/austin-${AUSTIN_VERSION}-gnu-linux-amd64.tar.xz -L | tar xJv
        curl -s https://github.com/loadimpact/k6/releases/download/v0.26.2/k6-v0.26.2-linux64.tar.gz -L | tar xvz
    elif [[ "$OSTYPE" == "darwin"* ]]
    then
        curl -s https://github.com/p403n1x87/austin/releases/download/v${AUSTIN_VERSION}/austin-${AUSTIN_VERSION}-mac64.zip -L -o ${PREFIX}/austin.zip
        unzip austin.zip
        rm -f austin.zip

        curl -s https://github.com/loadimpact/k6/releases/download/v0.26.2/k6-v0.26.2-mac.zip -L -o ${PREFIX}/k6.zip
        unzip k6.zip
        rm -f k6.zip
    fi
    chmod +x austin
popd

# Install ddtrace
pip install -e .

# Install diff-tool dependencies
pip install rich "austin-python~=1.4"
