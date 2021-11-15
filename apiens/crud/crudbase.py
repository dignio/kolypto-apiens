from __future__ import annotations

from collections import abc
from typing import TypeVar, Generic, ClassVar, Optional, Union

import sqlalchemy as sa
import sqlalchemy.orm
import sqlalchemy.orm.base
import sqlalchemy.sql.elements

import jessiql

from apiens.tools.sqlalchemy import get_history_proxy_for_instance
from .crudsettings import CrudSettings
from .crudparams import CrudParams
from .defs import PrimaryKeyDict


# SqlAlchemy instance object
SAInstanceT = TypeVar('SAInstanceT', bound=object)


class ModelQueryBase(Generic[SAInstanceT]):
    # CRUD settings: the static container for all the configuration of this CRUD handler.
    crudsettings: ClassVar[CrudSettings]

    # The database session to use
    ssn: sa.orm.Session

    def __init__(self, ssn: sa.orm.Session, params: CrudParams):
        self.ssn = ssn
        self.params = params


class QueryApi(ModelQueryBase[SAInstanceT]):
    query_object: jessiql.QueryObject

    def __init__(self,
                 ssn: sa.orm.Session,
                 params: CrudParams,
                 query_object: Union[jessiql.QueryObject, jessiql.QueryObjectDict] = None):
        super().__init__(ssn, params)

        # Query Object
        self.query_object = jessiql.QueryObject.ensure_query_object(query_object)

        # Init JessiQL
        # TODO: paginate or not?
        self.query = jessiql.QueryPage(self.query_object, self.crudsettings.Model)
        self.query.customize_statements.append(self._query_customize_statements)

    def list(self) -> list[dict]:
        self._filter_func = self.params.filter_many
        res = self.query.fetchall(self.ssn.get_bind())
        return res

    def get(self) -> Optional[dict]:
        self._filter_func = self.params.filter_one
        res = self.query.fetchone(self.ssn.get_bind())
        return res

    def count(self) -> int:
        self._filter_func = self.params.filter_many
        return self.query.count(self.ssn.get_bind())

    # The filter function that we decided to use
    _filter_func: abc.Callable[[], abc.Iterable[sa.sql.elements.BinaryExpression]]

    def _query_customize_statements(self, q: jessiql.Query, stmt: sa.sql.Select) -> sa.sql.Select:
        if q.query_level == 0:
            stmt = stmt.filter(*self._filter_func())
            return stmt
        else:
            return stmt
            # TODO: insert security hooks for deeper levels
            raise NotImplementedError


class MutateApiBase(ModelQueryBase[SAInstanceT]):
    # Use deep copying for historical `prev` objects?
    # Set to `True` if your Crud handler requires accessing the historical value of a mutable field (dict, etc)
    # Technically, it will use InstanceHistoryProxy(copy=True)
    COPY_INSTANCE_HISTORY: bool = False

    def _instance_hook_presave(self, new: Optional[SAInstanceT] = None, prev: Optional[SAInstanceT] = None):
        """ A hook called when an instance is going to be saved. Pre-flush, before signals.

        This hook is called before_flush:
        * on create(), after an instance has been constructed from the input
        * on update(), after an instance has been modified from the input
        * on delete(), after it has been loaded, before it's marked for deletion

        In all cases, this hook is called before any other signal is sent.
        Use it for fine-tuning created objects.

        Features:
        * The instance has just been modified
        * No other signal handlers have been fired
        * It is before-flush, so all the changes are still visible

        Here's how you know what's going on:
        * new and not old: create()
        * new and old: update()
        * not new and old: delete()

        Args:
            new: The instance being saved. `None` when delete()
            prev: The unmodified version of the instance. `None` when create()
        """

    # region Instance lookup & mutation

    def _find_instance(self) -> SAInstanceT:
        """ Get exactly one SqlAlchemy instance

        Raises:
            sa.exc.NoResultFound
            sa.exc.MultipleResultsFound
        """
        q = self.ssn.query(self.crudsettings.Model)
        q = q.filter(*self.params.filter_one())
        return q.one()

    def _create_instance(self, input_dict: dict) -> SAInstanceT:
        """ Create an SqlAlchemy instance """
        # Create
        instance = self._create_instance_from_input_dict(input_dict)

        # Hook
        self._instance_hook_presave(new=instance, prev=None)

        # Done
        return instance

    def _update_instance(self, instance: SAInstanceT, input_dict: dict) -> SAInstanceT:
        # Update
        instance = self._update_instance_from_input_dict(instance, input_dict)

        # Hook
        prev = get_history_proxy_for_instance(instance, copy=self.COPY_INSTANCE_HISTORY)
        self._instance_hook_presave(new=instance, prev=prev)

        # Done
        return instance

    def _delete_instance(self, instance: SAInstanceT) -> SAInstanceT:
        # Hook
        self._instance_hook_presave(new=None, prev=instance)

        # Done
        return instance

    # endregion

    # region Input dict handling for create & update

    def _create_instance_from_input_dict(self, input_dict: dict) -> SAInstanceT:
        """ Create a new SqlAlchemy instance from an `input_dict` fields

        Override to customize how new instances are created
        """
        return self.crudsettings.Model(**input_dict)

    def _update_instance_from_input_dict(self, instance: SAInstanceT, input_dict: dict) -> SAInstanceT:
        """ Modify an existing SqlAlchemy instance using field values from `input_dict`

        Override to customize how instances are updated
        """
        for key, value in input_dict.items():
            setattr(instance, key, value)  # triggers SqlAlchemy change detection logic
        return instance

    # endregion

    # region Mutated instance handling with SqlAlchemy Session

    def _session_create_instance_impl(self, instance: SAInstanceT) -> SAInstanceT:
        """ Session support for create() instance """
        self.ssn.add(instance)
        self.ssn.flush()
        return instance

    def _session_update_instance_impl(self, instance: SAInstanceT) -> SAInstanceT:
        """ Session support for update() instance """
        self.ssn.flush()
        return instance

    def _session_delete_instance_impl(self, instance: SAInstanceT) -> SAInstanceT:
        """ Session support for update() instance """
        # Workaround: remove all possible lazy loads and raiseloads. raiseload() is especially harmful here:
        # SqlAlchemy will do cascade deletions, and it will need the values of foreign key fields.
        # See: https://github.com/sqlalchemy/sqlalchemy/issues/5398
        self.ssn.refresh(instance)

        # Flush
        self.ssn.delete(instance)
        self.ssn.flush()

        # Done
        return instance

    # endregion


class MutateApi(MutateApiBase[SAInstanceT]):
    # TODO: mutation signals?

    # TODO: catch SqlAlchemy errors and rethrow them as Apiens errors?

    # TODO: @saves_custom_fields

    def create(self, input_dict: dict) -> PrimaryKeyDict:
        instance = self._create_instance(input_dict)
        instance = self._session_create_instance_impl(instance)
        return self._get_primary_key_dict(instance)

    def create_or_update(self, input_dict: dict) -> PrimaryKeyDict:
        pk_provided = set(input_dict) >= set(self.crudsettings.primary_key)
        if pk_provided:
            return self.update(input_dict)
        else:
            return self.create(input_dict)

    def update(self, input_dict: dict) -> PrimaryKeyDict:
        self.params.from_input_dict(input_dict)
        return self.update_id(input_dict)

    def update_id(self, input_dict: dict) -> PrimaryKeyDict:
        instance = self._find_instance()
        instance = self._update_instance(instance, input_dict)
        instance = self._session_update_instance_impl(instance)
        return self._get_primary_key_dict(instance)

    def delete(self) -> PrimaryKeyDict:
        instance = self._find_instance()
        pk = self._get_primary_key_dict(instance)
        instance = self._delete_instance(instance)
        instance = self._session_delete_instance_impl(instance)
        return pk

    # region Helpers

    def _get_primary_key_dict(self, instance: SAInstanceT) -> PrimaryKeyDict:
        return dict(
            zip(
                self.crudsettings.primary_key,
                sa.orm.base.instance_state(instance).identity,
            )
        )

    # endregion


class ReturningMutateApi(MutateApi, QueryApi):
    def create(self, user: dict) -> dict:
        id = super().create(user)
        # TODO: implement!
        return {'id': id, 'is_admin': True, 'login': 'kolypto', 'name': 'Mark'}

    def update(self, user: dict) -> dict:
        id = super().update(user)
        # TODO: implement!
        return {'id': id, 'is_admin': True, 'login': 'kolypto', 'name': 'Mark', **user}

    def update_id(self, id: int, user: dict) -> dict:
        id = super().update_id(id, user)
        # TODO: implement!
        return {'id': id, 'is_admin': True, 'login': 'kolypto', 'name': 'Mark', **user}

    def delete(self, id: int) -> dict:
        id = super().delete(id)
        # TODO: implement!
        return {'id': id, 'is_admin': True, 'login': 'kolypto', 'name': 'Mark'}
