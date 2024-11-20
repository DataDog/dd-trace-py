class Graph:
    def __init__(self):
        self.nodes = dict()
        self.edges = dict()

    def contains(self, key):
        return key in self.nodes

    def add_node(self, node):
        self.nodes[node.uid()] = node

    @classmethod
    def from_traces(cls, traces):
        print(f"Loading graph from traces: {traces}")
        global GRAPH
        graph = Graph()

        for trace in traces:
            for span in trace.spans:
                service = span.service
                if not graph.contains(service):
                    node = Node(classifier="service", name=service)
                    graph.add_node(node=node)
        
        return graph

class Node:
    def __init__(self, classifier=None, name=None):
        self.classifier = classifier
        self.name = name

    def __repr__(self):
        return f"{self.__dict__}"

    def uid(self):
        return (self.classifier, self.name)


class Edge:
    def __init__(self):
        pass

class Traversal:
    def __init__(self):
        pass