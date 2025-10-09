"""Test script to verify ddtrace-run imports sitecustomize."""
import sys

def main():
    # Check if sitecustomize was imported by ddtrace-run
    if 'ddtrace.bootstrap.sitecustomize' in sys.modules:
        print("SUCCESS: sitecustomize was imported by ddtrace-run")
        return 0
    else:
        print("ERROR: sitecustomize was not imported by ddtrace-run", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
