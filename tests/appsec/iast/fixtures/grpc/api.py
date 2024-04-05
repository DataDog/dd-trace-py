import grpc

from api_pb2 import Items, Item, Hello, HelloRequest, ApiRequest, MapTest, MapRequest, MsgMapRequest, MsgMapTest
from api_pb2 import Hello, HelloRequest, ApiRequest, MapTest
from api_pb2_grpc import ApiServicer, ApiStub


class ApiServer(ApiServicer):
    def getAll(self, request, context):
        data = []
        for i in range(1, request.length + 1):
            data.append(Item(id=i, name=f'name {i}'))
        return Items(items=data)

    def getStream(self, request, context):
        for i in range(1, request.length + 1):
            yield Item(id=i, name=f'name {i}')

    def sayHello(self, request, context):
        return Hello(message=f'Hello {request.name}!')

    def getMap(self, request, context):
        return MapTest(mapTest={1: "foo"})

    def getMsgMap(self, request, context):
        return MsgMapTest(msgMapTest={1: Item(id=1, name="mapitem")})



class ApiClient:
    def __init__(self, target):
        channel = grpc.insecure_channel(target)
        self.client = ApiStub(channel)

    def sayHello(self, name):
        response = self.client.sayHello(HelloRequest(name=name))
        return response.message

    def getAll(self, length):
        response = self.client.getAll(ApiRequest(length=length))
        return response.items

    def getStream(self, length):
        response = self.client.getStream(ApiRequest(length=length))
        return response

    def getMap(self, mapTest):
        response = self.client.getMap(MapRequest(key=1, value="one"))
        return response.mapTest

    def getMsgMap(self, msgMapTest):
        response = self.client.getMsgMap(MsgMapRequest(key=1, value=Item(id=1, name="mapitem")))
        return response.msgMapTest

