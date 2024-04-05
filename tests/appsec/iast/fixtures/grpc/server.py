import logging
from concurrent import futures

import grpc

from api import ApiServer
from api_pb2_grpc import add_ApiServicer_to_server

BACKEND_PORT = 6000

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    add_ApiServicer_to_server(ApiServer(), server)
    server.add_insecure_port(f'127.0.0.1:{BACKEND_PORT}')
    server.start()
    server.wait_for_termination()


if __name__ == '__main__':
    logging.basicConfig()
    serve()
