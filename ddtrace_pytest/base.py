"""Base proxy class for ddtrace pytest plugins."""
import os

class BasePytestPluginProxy:
    def __init__(self, plugin_module_path, plugin_name):
        self.plugin_name = plugin_name
        self.plugin_module_path = plugin_module_path
        self.enabled = os.getenv("DD_CIVISIBILITY_ENABLED", "true").lower() in ("true", "1")
        self._plugin_module = None
        self._load_plugin()
    
    def _load_plugin(self):
        """Load the actual plugin module if killswitch is enabled."""
        if not self.enabled:
            return
            
        try:
            import importlib
            self._plugin_module = importlib.import_module(self.plugin_module_path)
        except ImportError as e:
            print(f"Warning: Could not load {self.plugin_name} plugin: {e}")
            self._plugin_module = None
    
    def __getattr__(self, name):
        """Dynamically forward hook calls to the loaded plugin."""
        if not self.enabled:
            # Return appropriate no-op based on hook type
            if name == 'pytest_addoption':
                return self._noop_addoption
            elif name == 'pytest_configure':
                return self._noop_configure
            else:
                return self._noop_hook
        
        # Forward to the actual plugin if it has this attribute
        if self._plugin_module and hasattr(self._plugin_module, name):
            return getattr(self._plugin_module, name)
        
        # Return no-op if plugin doesn't have this hook
        return self._noop_hook
    
    def _noop_addoption(self, parser):
        """Base no-op for pytest_addoption - can be overridden."""
        pass
    
    def _noop_configure(self, config):
        """Base no-op for pytest_configure - can be overridden."""
        pass
    
    def _noop_hook(self, *args, **kwargs):
        """Generic no-op for other hooks."""
        return None