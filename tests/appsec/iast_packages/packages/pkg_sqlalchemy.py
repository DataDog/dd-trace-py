"""
sqlalchemy==2.0.30
https://pypi.org/project/sqlalchemy/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_sqlalchemy = Blueprint("package_sqlalchemy", __name__)


@pkg_sqlalchemy.route("/sqlalchemy")
def pkg_sqlalchemy_view():
    from sqlalchemy import Column
    from sqlalchemy import Integer
    from sqlalchemy import String
    from sqlalchemy import create_engine
    from sqlalchemy.orm import declarative_base
    from sqlalchemy.orm import sessionmaker

    response = ResultResponse(request.args.get("package_param"))

    try:
        Base = declarative_base()

        class User(Base):
            __tablename__ = "users"
            id = Column(Integer, primary_key=True)
            name = Column(String)
            age = Column(Integer)

        engine = create_engine("sqlite:///:memory:", echo=True)
        Base.metadata.create_all(engine)

        Session = sessionmaker(bind=engine)
        session = Session()

        new_user = User(name=response.package_param, age=65)
        session.add(new_user)
        session.commit()

        user = session.query(User).filter_by(name=response.package_param).first()

        response.result1 = {"id": user.id, "name": user.name, "age": user.age}
    except Exception as e:
        response.result1 = str(e)

    return response.json()


@pkg_sqlalchemy.route("/sqlalchemy_propagation")
def pkg_sqlalchemy_propagation_view():
    from sqlalchemy import Column
    from sqlalchemy import Integer
    from sqlalchemy import String
    from sqlalchemy import create_engine
    from sqlalchemy.orm import declarative_base

    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    try:
        Base = declarative_base()

        class User(Base):
            __tablename__ = "users"
            id = Column(Integer, primary_key=True)
            name = Column(String)
            age = Column(Integer)

        engine = create_engine("sqlite:///:memory:", echo=True)
        Base.metadata.create_all(engine)
        new_user = User(name=response.package_param, age=65)
        response.result1 = "OK" if is_pyobject_tainted(new_user.name) else "Error: new_user.name is not tainted"
    except Exception as e:
        response.result1 = str(e)

    return response.json()
