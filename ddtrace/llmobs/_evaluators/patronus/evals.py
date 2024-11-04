

class NoRacialBias:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return True
    
class Toxicity:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return True

class NoOpenAI:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return True
    

class Hallucination:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return True
    
class AnswerRelevance:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return True
    
