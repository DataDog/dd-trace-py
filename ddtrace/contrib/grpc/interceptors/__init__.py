# MIT License
#
# Copyright (c) 2017 Amit Saha
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import six
import abc


class UnaryUnaryServerInterceptor(six.with_metaclass(abc.ABCMeta)):
    @abc.abstractmethod
    def intercept_unary_unary_handler(self, handler, method, request, servicer_context):
        """
        Intercepts unary-unary RPCs on the service-side.
        :param handler: The handler to continue processing the RPC. It takes a request value and a ServicerContext
                        object and returns a response value.
        :param method: The full method name of the RPC.
        :param request: The request value for the RPC.
        :param servicer_context: The context of the current RPC.
        :return: The RPC response
        """
        raise NotImplementedError()


class UnaryStreamServerInterceptor(six.with_metaclass(abc.ABCMeta)):
    @abc.abstractmethod
    def intercept_unary_stream_handler(self, handler, method, request, servicer_context):
        """
        Intercepts unary-stream RPCs on the service-side.
        :param handler: The handler to continue processing the RPC. It takes a request value and a ServicerContext
                        object and returns a response value.
        :param method: The full method name of the RPC.
        :param request: The request value for the RPC.
        :param servicer_context: The context of the current RPC.
        :return: An iterator of RPC response values.
        """
        raise NotImplementedError()


class StreamUnaryServerInterceptor(six.with_metaclass(abc.ABCMeta)):
    @abc.abstractmethod
    def intercept_stream_unary_handler(self, handler, method, request_iterator, servicer_context):
        """
        Intercepts stream-unary RPCs on the service-side.
        :param handler: The handler to continue processing the RPC. It takes a request value and a ServicerContext
                        object and returns a response value.
        :param method: The full method name of the RPC.
        :param request_iterator: An iterator of request values for the RPC.
        :param servicer_context: The context of the current RPC.
        :return: The RPC response.
        """
        raise NotImplementedError()


class StreamStreamServerInterceptor(six.with_metaclass(abc.ABCMeta)):
    @abc.abstractmethod
    def intercept_stream_stream_handler(self, handler, method, request_iterator, servicer_context):
        """
        Intercepts stream-stream RPCs on the service-side.
        :param handler: The handler to continue processing the RPC. It takes a request value and a ServicerContext
                        object and returns an iterator of response values.
        :param method: The full method name of the RPC.
        :param request_iterator: An iterator of request values for the RPC.
        :param servicer_context: The context of the current RPC.
        :return: An iterator of RPC response values.
        """
        raise NotImplementedError()


def intercept_server(server, *interceptors):
    """
    Creates an intercepted server.
    :param server: A Server object
    :param interceptors: Zero or more objects of type UnaryUnaryServerInterceptor, UnaryStreamServerInterceptor,
                        StreamUnaryServerInterceptor, or StreamStreamServerInterceptor.
                        Interceptors are given control in the order they are listed.
    :return: A Server that intercepts each received RPC via the provided interceptors.
    :raises: TypeError: If interceptor does not derive from any of
               UnaryUnaryServerInterceptor,
               UnaryStreamServerInterceptor,
               StreamUnaryServerInterceptor, or
               StreamStreamServerInterceptor.
    """
    from . import _interceptor
    return _interceptor.intercept_server(server, *interceptors)
