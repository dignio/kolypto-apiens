from collections import abc
from contextlib import contextmanager
from dataclasses import dataclass

import graphql
import ariadne
import ariadne.asgi

import sqlalchemy as sa
import sqlalchemy.orm

import apiens.crud
import apiens.integration.ariadne.directives
from apiens.integration.ariadne.testing.query import graphql_query_sync

import jessiql
import jessiql.integration.graphql
from apiens.tools.sqlalchemy import db_transaction
from jessiql.util import sacompat
from jessiql.testing import created_tables, truncate_db_tables, insert

from tests.conftest import DATABASE_URL


def test_ariadne():
    """ Test CRUD API: create(), get() -- with errors returned as result payload """
    def main():
        # === Test: Create
        res = graphql_query_sync(schema,
                                 'mutation ($user: UserCreate!) { createUser(user: $user) { ok error { name } user { id } } }',
                                 user={'is_admin': False, 'login': 'root', 'name': 'Neo'})
        assert res == {
            'data': {
                'createUser': {
                    'ok': True,
                    'error': None,
                    'user': {'id': 1},
                }
            }
        }

        # === Test: Query
        res = graphql_query_sync(schema, 'query ($id: Int!) { getUser(id: $id) { id login } }', id=1)
        assert res == {
            'data': {
                'getUser': {'id': 1, 'login': 'root'}
            }
        }

    # Crud Params
    @dataclass
    class UserIdParams(apiens.crud.CrudParams):
        crudsettings = apiens.crud.CrudSettings(Model=User, debug=True)

        id: int

    # Resolver: query
    query = ariadne.QueryType()

    @query.field('getUser')
    def resolve_get_user(_, info: graphql.GraphQLResolveInfo, id: int):
        query_object = jessiql.integration.graphql.query_object_for(info)
        with Session() as ssn:
            api = apiens.crud.QueryApi(ssn, UserIdParams(id=id), query_object)
            res = api.get()
            return res

    # Resolver: mutation
    mutation = ariadne.MutationType()

    @mutation.field('createUser')
    def resolve_create_user(_, info: graphql.GraphQLResolveInfo, user: dict):
        with Session() as ssn, db_transaction(ssn):
            api = apiens.crud.MutateApi(ssn, UserIdParams(id=None))
            res = api.create(user)
            return {'ok': True, 'error': None, 'user': res}

    # Schema
    schema = ariadne.make_executable_schema(
        [GQL_SCHEMA, *GQL_SCHEMAS()],
        # bindables
        query, mutation,
        ariadne.snake_case_fallback_resolvers,
        directives={
            **apiens.integration.ariadne.directives.directives_map,
        }
    )
    # app = ariadne.asgi.GraphQL(schema, debug=True)

    with db_create():
        main()

# GraphQL definitions

# language=graphql
GQL_SCHEMA = ariadne.gql('''
type Query {
    getUser(id: Int!, query: QueryObjectInput): User!
    listUsers(query: QueryObjectInput): [User!]!
}

type Mutation {
    createUser(user: UserCreate!): UserMutationPayload!
    updateUserId(id: Int, user: UserUpdate!): UserMutationPayload!
    deleteUser(id: Int!): UserMutationPayload!
}

type Payload {
    ok: Boolean!
    error: ErrorObject
}

type UserMutationPayload @inherits(type: "Payload") {
    user: User
}

type UserBase {
    # rw fields
    login: String!
    name: String!
}

type User @inherits(type: "UserBase") {
    # rw ; +ro, const fields, +relations
    id: Int!
    is_admin: Boolean!
    # articles: [Article]
}

input UserCreate @inherits(type: "UserBase") {
    # rw ; +const fields, +relations
    is_admin: Boolean!
}

input UserUpdate @partial @inherits(type: "UserBase") {
    # rw ; +const fields, +relations; +skippable PK
    id: Int!
}
''')


def GQL_SCHEMAS() -> list[str]:
    """ Additional schemas to load """
    import jessiql.integration.graphql
    import apiens.structure.error
    from apiens.tools.graphql import directives

    return [
        ariadne.load_schema_from_path(*jessiql.integration.graphql.__path__),
        ariadne.load_schema_from_path(*apiens.structure.error.__path__),
        directives.partial.DIRECTIVE_SDL,
        directives.inherits.DIRECTIVE_SDL,
    ]


# SqlAlchemy models

Base = sacompat.declarative_base()


class User(Base):
    __tablename__ = 'u'

    id = sa.Column(sa.Integer, primary_key=True)
    is_admin = sa.Column(sa.Boolean, nullable=False)
    login = sa.Column(sa.String)
    name = sa.Column(sa.String)


# DB Engine
engine = sa.engine.create_engine(DATABASE_URL)


@contextmanager
def Session() -> sa.orm.Session:
    """ DB Session as a context manager """
    ssn = sa.orm.Session(bind=engine, autoflush=True, future=True)

    try:
        yield ssn
    finally:
        ssn.close()


@contextmanager
def db_create():
    with created_tables(engine, Base.metadata):
        yield


def db_cleanup(ssn: sa.orm.Session = None):
    truncate_db_tables(ssn.connection() if ssn else engine, Base.metadata)