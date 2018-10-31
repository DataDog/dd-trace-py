class AttrDict(dict):
    def __getattr__(self, key):
        if key in self:
            return self[key]
        return object.__getattribute__(self, key)

    def __setattr__(self, key, value):
        # Allow overwriting an existing attribute, e.g. `self.global_config = dict()`
        if hasattr(self, key):
            object.__setattr__(self, key, value)
        else:
            self[key] = value
