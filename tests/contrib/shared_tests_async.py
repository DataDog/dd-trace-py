from ddtrace import Pin


# DBM Shared Tests
async def _test_execute(dbm_comment, cursor, wrapped_instance):
    # test string queries
    await cursor.execute("select 'blah'")
    wrapped_instance.execute.assert_called_once_with(dbm_comment + "select 'blah'")
    wrapped_instance.reset_mock()

    # test byte string queries
    await cursor.execute(b"select 'blah'")
    wrapped_instance.execute.assert_called_once_with(dbm_comment.encode() + b"select 'blah'")
    wrapped_instance.reset_mock()


async def _test_execute_many(dbm_comment, cursor, wrapped_instance):
    # test string queries
    await cursor.executemany("select %s", (("foo",), ("bar",)))
    wrapped_instance.executemany.assert_called_once_with(dbm_comment + "select %s", (("foo",), ("bar",)))
    wrapped_instance.reset_mock()

    # test byte string queries
    await cursor.executemany(b"select %s", ((b"foo",), (b"bar",)))
    wrapped_instance.executemany.assert_called_once_with(dbm_comment.encode() + b"select %s", ((b"foo",), (b"bar",)))
    wrapped_instance.reset_mock()


async def _test_dbm_propagation_enabled(tracer, cursor, service):
    await cursor.execute("SELECT 1")
    spans = tracer.pop()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == f"{service}.query"

    assert span.get_tag("_dd.dbm_trace_injected") == "true"


async def _test_dbm_propagation_comment_with_global_service_name_configured(
    config, db_system, cursor, wrapped_instance, execute_many=True
):
    """tests if dbm comment is set in given db system"""
    db_name = config["db"]

    dbm_comment = (
        f"/*dddb='{db_name}',dddbs='{db_system}',dde='staging',ddh='127.0.0.1',ddps='orders-app',"
        "ddpv='v7343437-d7ac743'*/ "
    )
    await _test_execute(dbm_comment, cursor, wrapped_instance)
    if execute_many:
        await _test_execute_many(dbm_comment, cursor, wrapped_instance)


async def _test_dbm_propagation_comment_integration_service_name_override(
    config, cursor, wrapped_instance, execute_many=True
):
    """tests if dbm comment is set in mysql"""
    db_name = config["db"]

    dbm_comment = (
        f"/*dddb='{db_name}',dddbs='service-name-override',dde='staging',ddh='127.0.0.1',ddps='orders-app',"
        "ddpv='v7343437-d7ac743'*/ "
    )
    await _test_execute(dbm_comment, cursor, wrapped_instance)
    if execute_many:
        await _test_execute_many(dbm_comment, cursor, wrapped_instance)


async def _test_dbm_propagation_comment_pin_service_name_override(
    config, cursor, conn, tracer, wrapped_instance, execute_many=True
):
    """tests if dbm comment is set in mysql"""
    db_name = config["db"]

    Pin.override(conn, service="pin-service-name-override", tracer=tracer)
    Pin.override(cursor, service="pin-service-name-override", tracer=tracer)

    dbm_comment = (
        f"/*dddb='{db_name}',dddbs='pin-service-name-override',dde='staging',ddh='127.0.0.1',ddps='orders-app',"
        "ddpv='v7343437-d7ac743'*/ "
    )
    await _test_execute(dbm_comment, cursor, wrapped_instance)
    if execute_many:
        await _test_execute_many(dbm_comment, cursor, wrapped_instance)


async def _test_dbm_propagation_comment_peer_service_enabled(config, cursor, wrapped_instance, execute_many=True):
    """tests if dbm comment is set in mysql"""
    db_name = config["db"]

    dbm_comment = (
        f"/*dddb='{db_name}',dddbs='test',dde='staging',ddh='127.0.0.1',ddps='orders-app'," "ddpv='v7343437-d7ac743'*/ "
    )
    await _test_execute(dbm_comment, cursor, wrapped_instance)
    if execute_many:
        await _test_execute_many(dbm_comment, cursor, wrapped_instance)


async def _test_dbm_propagation_comment_with_peer_service_tag(
    config, cursor, wrapped_instance, peer_service_name, execute_many=True
):
    """tests if dbm comment is set in mysql"""
    db_name = config["db"]

    dbm_comment = (
        f"/*dddb='{db_name}',dddbs='test',dde='staging',ddh='127.0.0.1',ddprs='{peer_service_name}',ddps='orders-app',"
        "ddpv='v7343437-d7ac743'*/ "
    )
    await _test_execute(dbm_comment, cursor, wrapped_instance)
    if execute_many:
        await _test_execute_many(dbm_comment, cursor, wrapped_instance)
