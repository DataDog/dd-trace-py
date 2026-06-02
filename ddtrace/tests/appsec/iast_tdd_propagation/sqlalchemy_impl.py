#!/usr/bin/env python3

from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import StaticPool


engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

Base = declarative_base()


class User(Base):
    __tablename__ = "User"
    id = Column(Integer, primary_key=True)
    name = Column(String(30), nullable=True)


User.metadata.create_all(engine)


def execute_query(param):
    with engine.connect() as connection:
        connection.execute(text(param))


def execute_untainted_query(_):
    with engine.connect() as connection:
        connection.execute(text("SELECT * FROM User"))
