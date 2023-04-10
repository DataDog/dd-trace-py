import time
class DataStreamsCtx:
    def __init__(self, tags):
        now = time.time()
        self.tags = tags
        self.pathwayStartTp = now
        self.currentEdgeStart = now
        self.hash = 0

    def set_checkpoint(self, tags):
