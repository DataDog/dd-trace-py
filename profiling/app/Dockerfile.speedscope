FROM nginx
RUN apt-get update && apt-get install unzip wget
RUN wget https://github.com/jlfwong/speedscope/releases/download/v1.14.0/speedscope-1.14.0.zip
RUN unzip speedscope-1.14.0.zip && mv speedscope/* /usr/share/nginx/html