PYTHON_VERSION=3.10 && CONTRIB_NAME="flask" && CONTRIB_VERSION="2.0.2" && docker build --build-arg PYTHON_VERSION=${PYTHON_VERSION} --build-arg CONTRIB_NAME=${CONTRIB_NAME} --build-arg CONTRIB_VERSION=${CONTRIB_VERSION} -t ${PYTHON_VERSION}-${CONTRIB_NAME}-${CONTRIB_VERSION} .

docker run  --rm -p 5000:5000 -it ${PYTHON_VERSION}-${CONTRIB_NAME}-${CONTRIB_VERSION}


docker-compose build
docker-compose up -d