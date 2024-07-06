#!/usr/bin/env bash -eu

set -eu

PREFIX=${1}
AUSTIN_VERSION="3.6"
K6_VERSION="0.26.2"

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
python3.10 -m venv ${PREFIX}
source ${PREFIX}/bin/activate
pip install pip --upgrade

# Install the application
git clone https://github.com/DataDog/trace-examples.git ${PREFIX}/trace-examples
pushd ${PREFIX}/trace-examples/
    git checkout origin/django-simple
    pushd python/django/django-simple
        pip install -r requirements/production.txt
        python manage.py migrate
    popd
popd

# Install k6
pushd ${PREFIX}
    if [[ "$OSTYPE" == "linux-gnu"* ]]
    then
        curl -s https://github.com/loadimpact/k6/releases/download/v${K6_VERSION}/k6-v${K6_VERSION}-linux64.tar.gz -L | tar xvz
    elif [[ "$OSTYPE" == "darwin"* ]]
    then
        curl -s https://github.com/loadimpact/k6/releases/download/v${K6_VERSION}/k6-v${K6_VERSION}-mac.zip -L -o ${PREFIX}/k6.zip
        unzip k6.zip
        rm -f k6.zip
    fi
    
popd

# Install austin
pip install "austin-dist~=$AUSTIN_VERSION"

# Install ddtrace
pip install -e .

# Install diff-tool dependencies
pip install rich "austin-python~=1.4"
