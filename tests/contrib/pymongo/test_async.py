import asyncio

import pymongo
import pytest

from ddtrace.contrib.internal.pymongo.patch import patch
from ddtrace.contrib.internal.pymongo.patch import unpatch
from ddtrace.ext import SpanTypes
from tests.contrib.asyncio.utils import AsyncioTestCase
from tests.contrib.asyncio.utils import mark_asyncio
from tests.utils import assert_is_measured

from ..config import MONGO_CONFIG
from .test import CommandCapture


pytestmark = pytest.mark.skipif(
    pymongo.version_tuple < (4, 12),
    reason="Async pymongo support requires pymongo >= 4.12",
)


if pymongo.version_tuple >= (4, 12):
    from pymongo.asynchronous.mongo_client import AsyncMongoClient
else:
    # Fallback for older versions (though tests will be skipped)
    AsyncMongoClient = None


class TestAsyncPymongo(AsyncioTestCase):
    def setUp(self):
        super().setUp()
        patch()

    def tearDown(self):
        super().tearDown()
        unpatch()

    @mark_asyncio
    async def test_async_unacknowledged_bulk_write(self):
        if pymongo.version_tuple < (4, 18):
            self.skipTest("Regression coverage for PyMongo 4.18 bulk executors")
        client = AsyncMongoClient(port=MONGO_CONFIG["port"])
        try:
            collection = client.testdb.async_unacknowledged_bulk
            await collection.drop()
            collection = collection.with_options(write_concern=pymongo.WriteConcern(w=0))
            self.pop_spans()
            result = await collection.insert_many([{"_id": 1}, {"_id": 2}], ordered=False)
            assert not result.acknowledged
            result = await collection.bulk_write(
                [pymongo.UpdateOne({"_id": 1}, {"$set": {"updated": True}}), pymongo.DeleteOne({"_id": 2})],
                ordered=False,
            )
            assert not result.acknowledged
            cmd_spans = [
                s
                for s in self.pop_spans()
                if s.name == "pymongo.cmd" and s.get_tag("mongodb.collection") == "async_unacknowledged_bulk"
            ]
            assert {s.resource.split()[0] for s in cmd_spans} == {"insert", "update", "delete"}
            assert all(s.error == 0 and s.get_metric("db.row_count") is None for s in cmd_spans)
            documents = [doc async for doc in client.testdb.async_unacknowledged_bulk.find()]
            assert documents == [{"_id": 1, "updated": True}]
        finally:
            await client.close()

    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_DBM_PROPAGATION_MODE="full"))
    @mark_asyncio
    async def test_async_client_bulk_write_dbm(self):
        if pymongo.version_tuple < (4, 18):
            self.skipTest("Client bulk executor tracing requires PyMongo 4.18+")
        capture = CommandCapture()
        client = AsyncMongoClient(port=MONGO_CONFIG["port"], event_listeners=[capture])
        try:
            if (await client.server_info())["versionArray"][0] < 8:
                self.skipTest("Client bulk writes require MongoDB 8.0+")
            for acknowledged in (True, False):
                await client.testdb.async_client_bulk.drop()
                await client.testdb.async_client_bulk_other.drop()
                capture.clear()
                self.pop_spans()
                result = await client.bulk_write(
                    [
                        pymongo.InsertOne({"_id": 1}, namespace="testdb.async_client_bulk"),
                        pymongo.InsertOne({"_id": 2}, namespace="testdb.async_client_bulk_other"),
                    ],
                    ordered=False,
                    write_concern=pymongo.WriteConcern(w=int(acknowledged)),
                )
                assert result.acknowledged == acknowledged
                spans = [s for s in self.pop_spans() if s.name == "pymongo.cmd"]
                assert len(spans) == 1
                span = spans[0]
                assert span.resource == "bulkWrite 1"
                assert span.get_tag("mongodb.db") == "admin"
                assert span.error == 0
                assert span.get_tag("_dd.dbm_trace_injected") == "true"
                commands = [cmd for _, name, cmd in capture.started_commands if name == "bulkWrite"]
                assert len(commands) == 1
                assert f"traceparent='{span.context._traceparent}'" in commands[0]["comment"]
                if acknowledged:
                    assert result.inserted_count == 2
                else:
                    assert span.get_metric("db.row_count") is None
                assert await client.testdb.async_client_bulk.find_one() == {"_id": 1}
                assert await client.testdb.async_client_bulk_other.find_one() == {"_id": 2}
        finally:
            await client.close()

    @mark_asyncio
    async def test_async_insert_find(self):
        client = AsyncMongoClient(port=MONGO_CONFIG["port"])
        try:
            db = client["testdb"]
            await db.drop_collection("teams")

            teams = [{"name": "Team1"}, {"name": "Team2"}, {"name": "Team3"}]
            await db.teams.insert_one(teams[0])
            await db.teams.insert_many(teams[1:])

            count = len([_ async for _ in db.teams.find()])
            assert count == 3

            queried = [doc async for doc in db.teams.find({"name": "Team1"})]
            assert len(queried) == 1
            assert queried[0]["name"] == "Team1"

            spans = self.pop_spans()
            # Filter out checkout spans like sync tests do
            cmd_spans = [s for s in spans if s.name == "pymongo.cmd"]
            assert len(cmd_spans) >= 4

            # Filter to spans with collections (exclude internal commands like ismaster)
            teams_spans = [s for s in cmd_spans if s.get_tag("mongodb.collection") == "teams"]
            assert len(teams_spans) >= 4

            for span in teams_spans:
                assert_is_measured(span)
                assert span.service == "pymongo"
                assert span.span_type == SpanTypes.MONGODB
                assert span.get_tag("component") == "pymongo"
                assert span.get_tag("span.kind") == "client"
                assert span.get_tag("db.system") == "mongodb"
                assert span.get_tag("mongodb.collection") == "teams"
                assert span.get_tag("mongodb.db") == "testdb"
                assert span.get_tag("out.host")
                assert span.get_metric("network.destination.port")

            find_spans = [s for s in teams_spans if "find" in s.resource]
            assert len(find_spans) >= 2
            find_all = [s for s in find_spans if s.get_tag("mongodb.query") is None][0]
            find_query = [s for s in find_spans if s.get_tag("mongodb.query") is not None][0]
            assert "find teams" in find_all.resource
            assert 'find teams {"name": "?"}' in find_query.resource
        finally:
            await client.close()
            await asyncio.sleep(0.1)

    @mark_asyncio
    async def test_async_mongodb_obfuscation_disabled_find_operation(self):
        client = AsyncMongoClient(port=MONGO_CONFIG["port"])
        try:
            with self.override_config("pymongo", dict(_mongodb_obfuscation=False)):
                db = client["testdb"]
                await db.drop_collection("teams")
                await db.teams.insert_one({"name": "Team1"})
                queried = [doc async for doc in db.teams.find({"name": "Team1"})]
                assert len(queried) == 1

            spans = self.pop_spans()
            find_with_query = [s for s in spans if s.name == "pymongo.cmd" and s.get_tag("mongodb.query") is not None]
            assert len(find_with_query) == 1
            span = find_with_query[0]
            assert span.resource == 'find teams {"name": "?"}'
            assert span.get_tag("mongodb.query") == '{"name": "Team1"}'
        finally:
            await client.close()
            await asyncio.sleep(0.1)

    @mark_asyncio
    async def test_async_update(self):
        client = AsyncMongoClient(port=MONGO_CONFIG["port"])
        try:
            db = client["testdb"]
            await db.drop_collection("songs")
            await db.songs.insert_many([{"name": "Song1", "artist": "A"}, {"name": "Song2", "artist": "A"}])

            result = await db.songs.update_many({"artist": "A"}, {"$set": {"artist": "B"}})
            assert result.matched_count == 2

            spans = self.pop_spans()
            update_spans = [s for s in spans if s.name == "pymongo.cmd" and "update" in s.resource]
            assert len(update_spans) > 0
            assert 'update songs {"artist": "?"}' in update_spans[0].resource
            assert_is_measured(update_spans[0])
        finally:
            await client.close()
            await asyncio.sleep(0.1)

    @mark_asyncio
    async def test_async_delete(self):
        client = AsyncMongoClient(port=MONGO_CONFIG["port"])
        try:
            db = client["testdb"]
            collection_name = "test.songs"
            await db.drop_collection(collection_name)
            songs = db[collection_name]
            await songs.insert_many([{"artist": "A"}, {"artist": "A"}, {"artist": "B"}])

            await songs.delete_one({"artist": "A"})
            assert await songs.count_documents({"artist": "A"}) == 1

            await songs.delete_many({"artist": "B"})
            assert await songs.count_documents({"artist": "B"}) == 0

            spans = self.pop_spans()
            delete_spans = [s for s in spans if s.name == "pymongo.cmd" and "delete" in s.resource]
            assert len(delete_spans) >= 2
            assert any(f'delete {collection_name} {{"artist": "?"}}' in s.resource for s in delete_spans)
            assert_is_measured(delete_spans[0])
        finally:
            await client.close()
            await asyncio.sleep(0.1)

    @mark_asyncio
    async def test_async_rowcount(self):
        client = AsyncMongoClient(port=MONGO_CONFIG["port"])
        try:
            db = client["testdb"]
            await db.songs.delete_many({})
            await db.songs.insert_many([{"name": "Song1"}, {"name": "Song2"}])

            assert len([doc async for doc in db.songs.find({"name": "Song1"})]) == 1
            assert len([doc async for doc in db.songs.find()]) == 2

            spans = self.pop_spans()
            find_spans = [s for s in spans if s.name == "pymongo.cmd" and "find" in s.resource]
            one_row = [s for s in find_spans if '{"name": "?"}' in s.resource][0]
            two_row = [s for s in find_spans if s.get_metric("db.row_count") == 2][0]

            assert one_row.get_metric("db.row_count") == 1
            assert two_row.get_metric("db.row_count") == 2
        finally:
            await client.close()
            await asyncio.sleep(0.1)

    @mark_asyncio
    async def test_async_span_parenting(self):
        from ddtrace.contrib.internal.pymongo.utils import _CHECKOUT_FN_NAME

        client = AsyncMongoClient(port=MONGO_CONFIG["port"], maxPoolSize=1)
        try:
            db = client["testdb"]
            await db.drop_collection("test_socket")
            await db.test_socket.insert_one({"name": "test1"})

            spans = self.pop_spans()
            # Should have at least one checkout span and one cmd span per operation
            assert len(spans) >= 2

            checkout_spans = [s for s in spans if s.name == f"pymongo.{_CHECKOUT_FN_NAME}"]
            cmd_spans = [s for s in spans if s.name == "pymongo.cmd"]

            assert len(checkout_spans) >= 1
            assert len(cmd_spans) >= 1

            # Verify checkout span metadata
            checkout_span = checkout_spans[0]
            assert checkout_span.service == "pymongo"
            assert checkout_span.span_type == SpanTypes.MONGODB
            assert checkout_span.get_tag("out.host") == "localhost"
            assert checkout_span.get_tag("component") == "pymongo"
            assert checkout_span.get_tag("span.kind") == "client"
            assert checkout_span.get_metric("network.destination.port") == MONGO_CONFIG["port"]
            assert checkout_span.get_tag("db.system") == "mongodb"
            assert_is_measured(checkout_span)
        finally:
            await client.close()
            await asyncio.sleep(0.1)
