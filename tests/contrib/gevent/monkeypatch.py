import gevent.monkey


gevent.monkey.patch_all()
print("Test success")
