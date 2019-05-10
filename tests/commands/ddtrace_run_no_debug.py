from ddtrace import tracer

if __name__ == '__main__':
    assert not tracer.debug_logging
    print('Test success')
