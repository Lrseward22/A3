# testing/suite/test_ddl.py
# Copyright (C) 2005-2025 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
# mypy: ignore-errors

import random

from . import testing
from .. import config
from .. import fixtures
from .. import util
from ..assertions import eq_
from ..assertions import is_false
from ..assertions import is_true
from ..config import requirements
from ..schema import Table
from ... import CheckConstraint
from ... import Column
from ... import ForeignKeyConstraint
from ... import Index
from ... import inspect
from ... import Integer
from ... import schema
from ... import String
from ... import UniqueConstraint


class TableDDLTest(fixtures.TestBase):
    __backend__ = True

    def _simple_fixture(self, schema=None):
        return Table(
            "test_table",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=False),
            Column("data", String(50)),
            schema=schema,
        )

    def _underscore_fixture(self):
        return Table(
            "_test_table",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=False),
            Column("_data", String(50)),
        )

    def _table_index_fixture(self, schema=None):
        table = self._simple_fixture(schema=schema)
        idx = Index("test_index", table.c.data)
        return table, idx

    def _simple_roundtrip(self, table):
        with config.db.begin() as conn:
            conn.execute(table.insert().values((1, "some data")))
            result = conn.execute(table.select())
            eq_(result.first(), (1, "some data"))

    @requirements.create_table
    @util.provide_metadata
    def test_create_table(self):
        table = self._simple_fixture()
        table.create(config.db, checkfirst=False)
        self._simple_roundtrip(table)

    @requirements.create_table
    @requirements.schemas
    @util.provide_metadata
    def test_create_table_schema(self):
        table = self._simple_fixture(schema=config.test_schema)
        table.create(config.db, checkfirst=False)
        self._simple_roundtrip(table)

    @requirements.drop_table
    @util.provide_metadata
    def test_drop_table(self):
        table = self._simple_fixture()
        table.create(config.db, checkfirst=False)
        table.drop(config.db, checkfirst=False)

    @requirements.create_table
    @util.provide_metadata
    def test_underscore_names(self):
        table = self._underscore_fixture()
        table.create(config.db, checkfirst=False)
        self._simple_roundtrip(table)

    @requirements.comment_reflection
    @util.provide_metadata
    def test_add_table_comment(self, connection):
        table = self._simple_fixture()
        table.create(connection, checkfirst=False)
        table.comment = "a comment"
        connection.execute(schema.SetTableComment(table))
        eq_(
            inspect(connection).get_table_comment("test_table"),
            {"text": "a comment"},
        )

    @requirements.comment_reflection
    @util.provide_metadata
    def test_drop_table_comment(self, connection):
        table = self._simple_fixture()
        table.create(connection, checkfirst=False)
        table.comment = "a comment"
        connection.execute(schema.SetTableComment(table))
        connection.execute(schema.DropTableComment(table))
        eq_(
            inspect(connection).get_table_comment("test_table"), {"text": None}
        )

    @requirements.table_ddl_if_exists
    @util.provide_metadata
    def test_create_table_if_not_exists(self, connection):
        table = self._simple_fixture()

        connection.execute(schema.CreateTable(table, if_not_exists=True))

        is_true(inspect(connection).has_table("test_table"))
        connection.execute(schema.CreateTable(table, if_not_exists=True))

    @requirements.index_ddl_if_exists
    @util.provide_metadata
    def test_create_index_if_not_exists(self, connection):
        table, idx = self._table_index_fixture()

        connection.execute(schema.CreateTable(table, if_not_exists=True))
        is_true(inspect(connection).has_table("test_table"))
        is_false(
            "test_index"
            in [
                ix["name"]
                for ix in inspect(connection).get_indexes("test_table")
            ]
        )

        connection.execute(schema.CreateIndex(idx, if_not_exists=True))

        is_true(
            "test_index"
            in [
                ix["name"]
                for ix in inspect(connection).get_indexes("test_table")
            ]
        )

        connection.execute(schema.CreateIndex(idx, if_not_exists=True))

    @requirements.table_ddl_if_exists
    @util.provide_metadata
    def test_drop_table_if_exists(self, connection):
        table = self._simple_fixture()

        table.create(connection)

        is_true(inspect(connection).has_table("test_table"))

        connection.execute(schema.DropTable(table, if_exists=True))

        is_false(inspect(connection).has_table("test_table"))

        connection.execute(schema.DropTable(table, if_exists=True))

    @requirements.index_ddl_if_exists
    @util.provide_metadata
    def test_drop_index_if_exists(self, connection):
        table, idx = self._table_index_fixture()

        table.create(connection)

        is_true(
            "test_index"
            in [
                ix["name"]
                for ix in inspect(connection).get_indexes("test_table")
            ]
        )

        connection.execute(schema.DropIndex(idx, if_exists=True))

        is_false(
            "test_index"
            in [
                ix["name"]
                for ix in inspect(connection).get_indexes("test_table")
            ]
        )

        connection.execute(schema.DropIndex(idx, if_exists=True))


class FutureTableDDLTest(fixtures.FutureEngineMixin, TableDDLTest):
    pass


class LongNameBlowoutTest(fixtures.TestBase):
    """test the creation of a variety of DDL structures and ensure
    label length limits pass on backends

    """

    __backend__ = True

    def fk(self, metadata, connection):
        convention = {
            "fk": "foreign_key_%(table_name)s_"
            "%(column_0_N_name)s_"
            "%(referred_table_name)s_"
            + (
                "_".join(
                    "".join(random.choice("abcdef") for j in range(20))
                    for i in range(10)
                )
            ),
        }
        metadata.naming_convention = convention

        Table(
            "a_things_with_stuff",
            metadata,
            Column("id_long_column_name", Integer, primary_key=True),
            test_needs_fk=True,
        )

        cons = ForeignKeyConstraint(
            ["aid"], ["a_things_with_stuff.id_long_column_name"]
        )
        Table(
            "b_related_things_of_value",
            metadata,
            Column(
                "aid",
            ),
            cons,
            test_needs_fk=True,
        )
        actual_name = cons.name

        metadata.create_all(connection)

        if testing.requires.foreign_key_constraint_name_reflection.enabled:
            insp = inspect(connection)
            fks = insp.get_foreign_keys("b_related_things_of_value")
            reflected_name = fks[0]["name"]

            return actual_name, reflected_name
        else:
            return actual_name, None

    def pk(self, metadata, connection):
        convention = {
            "pk": "primary_key_%(table_name)s_"
            "%(column_0_N_name)s"
            + (
                "_".join(
                    "".join(random.choice("abcdef") for j in range(30))
                    for i in range(10)
                )
            ),
        }
        metadata.naming_convention = convention

        a = Table(
            "a_things_with_stuff",
            metadata,
            Column("id_long_column_name", Integer, primary_key=True),
            Column("id_another_long_name", Integer, primary_key=True),
        )
        cons = a.primary_key
        actual_name = cons.name

        metadata.create_all(connection)
        insp = inspect(connection)
        pk = insp.get_pk_constraint("a_things_with_stuff")
        reflected_name = pk["name"]
        return actual_name, reflected_name

    def ix(self, metadata, connection):
        convention = {
            "ix": "index_%(table_name)s_"
            "%(column_0_N_name)s"
            + (
                "_".join(
                    "".join(random.choice("abcdef") for j in range(30))
                    for i in range(10)
                )
            ),
        }
        metadata.naming_convention = convention

        a = Table(
            "a_things_with_stuff",
            metadata,
            Column("id_long_column_name", Integer, primary_key=True),
            Column("id_another_long_name", Integer),
        )
        cons = Index(None, a.c.id_long_column_name, a.c.id_another_long_name)
        actual_name = cons.name

        metadata.create_all(connection)
        insp = inspect(connection)
        ix = insp.get_indexes("a_things_with_stuff")
        reflected_name = ix[0]["name"]
        return actual_name, reflected_name

    def uq(self, metadata, connection):
        convention = {
            "uq": "unique_constraint_%(table_name)s_"
            "%(column_0_N_name)s"
            + (
                "_".join(
                    "".join(random.choice("abcdef") for j in range(30))
                    for i in range(10)
                )
            ),
        }
        metadata.naming_convention = convention

        cons = UniqueConstraint("id_long_column_name", "id_another_long_name")
        Table(
            "a_things_with_stuff",
            metadata,
            Column("id_long_column_name", Integer, primary_key=True),
            Column("id_another_long_name", Integer),
            cons,
        )
        actual_name = cons.name

        metadata.create_all(connection)
        insp = inspect(connection)
        uq = insp.get_unique_constraints("a_things_with_stuff")
        reflected_name = uq[0]["name"]
        return actual_name, reflected_name

    def ck(self, metadata, connection):
        convention = {
            "ck": "check_constraint_%(table_name)s"
            + (
                "_".join(
                    "".join(random.choice("abcdef") for j in range(30))
                    for i in range(10)
                )
            ),
        }
        metadata.naming_convention = convention

        cons = CheckConstraint("some_long_column_name > 5")
        Table(
            "a_things_with_stuff",
            metadata,
            Column("id_long_column_name", Integer, primary_key=True),
            Column("some_long_column_name", Integer),
            cons,
        )
        actual_name = cons.name

        metadata.create_all(connection)
        insp = inspect(connection)
        ck = insp.get_check_constraints("a_things_with_stuff")
        reflected_name = ck[0]["name"]
        return actual_name, reflected_name

    @testing.combinations(
        ("fk",),
        ("pk",),
        ("ix",),
        ("ck", testing.requires.check_constraint_reflection.as_skips()),
        ("uq", testing.requires.unique_constraint_reflection.as_skips()),
        argnames="type_",
    )
    def test_long_convention_name(self, type_, metadata, connection):
        actual_name, reflected_name = getattr(self, type_)(
            metadata, connection
        )

        assert len(actual_name) > 255

        if reflected_name is not None:
            overlap = actual_name[0 : len(reflected_name)]
            if len(overlap) < len(actual_name):
                eq_(overlap[0:-5], reflected_name[0 : len(overlap) - 5])
            else:
                eq_(overlap, reflected_name)


__all__ = ("TableDDLTest", "FutureTableDDLTest", "LongNameBlowoutTest")
