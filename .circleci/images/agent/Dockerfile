FROM kyleverhoog/dd-trace-test-agent:latest

# docker build -t myagent:0.1 -f .circleci/images/agent/Dockerfile .

ADD tests/snapshots /snaps
WORKDIR /usr/src/agent

ENV SNAPSHOT_CI 1  # set CI mode
ENV SNAPSHOT_DIR /snaps
ENTRYPOINT ["java", "-jar", "app-standalone.jar"]
