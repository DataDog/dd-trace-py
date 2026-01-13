if __name__ == "__main__":
    # have to import celery in order to have the post-import hooks run
    import celery

    assert celery.Celery.__datadog_patch
    print("Test success")
