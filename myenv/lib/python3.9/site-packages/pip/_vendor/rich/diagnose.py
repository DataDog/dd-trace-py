if __name__ == "__main__":  # pragma: no cover
    from pip._vendor.rich import inspect
    from pip._vendor.rich.console import Console

    console = Console()
    inspect(console)
