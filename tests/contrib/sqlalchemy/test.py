
# 3p
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
)

# project

from ddtrace import Tracer
from ddtrace.contrib.sqlalchemy import trace_engine
from ...test_tracer import DummyWriter


Base = declarative_base()


class Player(Base):

    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    name = Column(String)


def test():
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    engine = create_engine('sqlite:///:memory:', echo=True)

    trace_engine(engine, tracer, service="foo")


    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # do an ORM query
    wayne = Player(id=1, name="wayne")
    session.add(wayne)
    session.commit()

    # do a regular old query
    conn = engine.connect()
    conn.execute("select * from users")

    try:
        conn.execute("select * from foo_Bah_blah")
    except Exception:
        pass

    spans = writer.pop()
    for span in spans:
        print span.pprint()
