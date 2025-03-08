# orm/unitofwork.py
# Copyright (C) 2005-2025 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
# mypy: ignore-errors


"""The internals for the unit of work system.

The session's flush() process passes objects to a contextual object
here, which assembles flush tasks based on mappers and their properties,
organizes them in order of dependency, and executes.

"""

from __future__ import annotations

from typing import Any
from typing import Dict
from typing import Optional
from typing import Set
from typing import TYPE_CHECKING

from . import attributes
from . import exc as orm_exc
from . import util as orm_util
from .. import event
from .. import util
from ..util import topological


if TYPE_CHECKING:
    from .dependency import DependencyProcessor
    from .interfaces import MapperProperty
    from .mapper import Mapper
    from .session import Session
    from .session import SessionTransaction
    from .state import InstanceState


def track_cascade_events(descriptor, prop):
    """Establish event listeners on object attributes which handle
    cascade-on-set/append.

    """
    key = prop.key

    def append(state, item, initiator, **kw):
        # process "save_update" cascade rules for when
        # an instance is appended to the list of another instance

        if item is None:
            return

        sess = state.session
        if sess:
            if sess._warn_on_events:
                sess._flush_warning("collection append")

            prop = state.manager.mapper._props[key]
            item_state = attributes.instance_state(item)

            if (
                prop._cascade.save_update
                and (key == initiator.key)
                and not sess._contains_state(item_state)
            ):
                sess._save_or_update_state(item_state)
        return item

    def remove(state, item, initiator, **kw):
        if item is None:
            return

        sess = state.session

        prop = state.manager.mapper._props[key]

        if sess and sess._warn_on_events:
            sess._flush_warning(
                "collection remove"
                if prop.uselist
                else "related attribute delete"
            )

        if (
            item is not None
            and item is not attributes.NEVER_SET
            and item is not attributes.PASSIVE_NO_RESULT
            and prop._cascade.delete_orphan
        ):
            # expunge pending orphans
            item_state = attributes.instance_state(item)

            if prop.mapper._is_orphan(item_state):
                if sess and item_state in sess._new:
                    sess.expunge(item)
                else:
                    # the related item may or may not itself be in a
                    # Session, however the parent for which we are catching
                    # the event is not in a session, so memoize this on the
                    # item
                    item_state._orphaned_outside_of_session = True

    def set_(state, newvalue, oldvalue, initiator, **kw):
        # process "save_update" cascade rules for when an instance
        # is attached to another instance
        if oldvalue is newvalue:
            return newvalue

        sess = state.session
        if sess:
            if sess._warn_on_events:
                sess._flush_warning("related attribute set")

            prop = state.manager.mapper._props[key]
            if newvalue is not None:
                newvalue_state = attributes.instance_state(newvalue)
                if (
                    prop._cascade.save_update
                    and (key == initiator.key)
                    and not sess._contains_state(newvalue_state)
                ):
                    sess._save_or_update_state(newvalue_state)

            if (
                oldvalue is not None
                and oldvalue is not attributes.NEVER_SET
                and oldvalue is not attributes.PASSIVE_NO_RESULT
                and prop._cascade.delete_orphan
            ):
                # possible to reach here with attributes.NEVER_SET ?
                oldvalue_state = attributes.instance_state(oldvalue)

                if oldvalue_state in sess._new and prop.mapper._is_orphan(
                    oldvalue_state
                ):
                    sess.expunge(oldvalue)
        return newvalue

    event.listen(
        descriptor, "append_wo_mutation", append, raw=True, include_key=True
    )
    event.listen(
        descriptor, "append", append, raw=True, retval=True, include_key=True
    )
    event.listen(
        descriptor, "remove", remove, raw=True, retval=True, include_key=True
    )
    event.listen(
        descriptor, "set", set_, raw=True, retval=True, include_key=True
    )


class UOWTransaction:
    session: Session
    transaction: SessionTransaction
    attributes: Dict[str, Any]
    deps: util.defaultdict[Mapper[Any], Set[DependencyProcessor]]
    mappers: util.defaultdict[Mapper[Any], Set[InstanceState[Any]]]

    def __init__(self, session: Session):
        self.session = session

        # dictionary used by external actors to
        # store arbitrary state information.
        self.attributes = {}

        # dictionary of mappers to sets of
        # DependencyProcessors, which are also
        # set to be part of the sorted flush actions,
        # which have that mapper as a parent.
        self.deps = util.defaultdict(set)

        # dictionary of mappers to sets of InstanceState
        # items pending for flush which have that mapper
        # as a parent.
        self.mappers = util.defaultdict(set)

        # a dictionary of Preprocess objects, which gather
        # additional states impacted by the flush
        # and determine if a flush action is needed
        self.presort_actions = {}

        # dictionary of PostSortRec objects, each
        # one issues work during the flush within
        # a certain ordering.
        self.postsort_actions = {}

        # a set of 2-tuples, each containing two
        # PostSortRec objects where the second
        # is dependent on the first being executed
        # first
        self.dependencies = set()

        # dictionary of InstanceState-> (isdelete, listonly)
        # tuples, indicating if this state is to be deleted
        # or insert/updated, or just refreshed
        self.states = {}

        # tracks InstanceStates which will be receiving
        # a "post update" call.  Keys are mappers,
        # values are a set of states and a set of the
        # columns which should be included in the update.
        self.post_update_states = util.defaultdict(lambda: (set(), set()))

    @property
    def has_work(self):
        return bool(self.states)

    def was_already_deleted(self, state):
        """Return ``True`` if the given state is expired and was deleted
        previously.
        """
        if state.expired:
            try:
                state._load_expired(state, attributes.PASSIVE_OFF)
            except orm_exc.ObjectDeletedError:
                self.session._remove_newly_deleted([state])
                return True
        return False

    def is_deleted(self, state):
        """Return ``True`` if the given state is marked as deleted
        within this uowtransaction."""

        return state in self.states and self.states[state][0]

    def memo(self, key, callable_):
        if key in self.attributes:
            return self.attributes[key]
        else:
            self.attributes[key] = ret = callable_()
            return ret

    def remove_state_actions(self, state):
        """Remove pending actions for a state from the uowtransaction."""

        isdelete = self.states[state][0]

        self.states[state] = (isdelete, True)

    def get_attribute_history(
        self, state, key, passive=attributes.PASSIVE_NO_INITIALIZE
    ):
        """Facade to attributes.get_state_history(), including
        caching of results."""

        hashkey = ("history", state, key)

        # cache the objects, not the states; the strong reference here
        # prevents newly loaded objects from being dereferenced during the
        # flush process

        if hashkey in self.attributes:
            history, state_history, cached_passive = self.attributes[hashkey]
            # if the cached lookup was "passive" and now
            # we want non-passive, do a non-passive lookup and re-cache

            if (
                not cached_passive & attributes.SQL_OK
                and passive & attributes.SQL_OK
            ):
                impl = state.manager[key].impl
                history = impl.get_history(
                    state,
                    state.dict,
                    attributes.PASSIVE_OFF
                    | attributes.LOAD_AGAINST_COMMITTED
                    | attributes.NO_RAISE,
                )
                if history and impl.uses_objects:
                    state_history = history.as_state()
                else:
                    state_history = history
                self.attributes[hashkey] = (history, state_history, passive)
        else:
            impl = state.manager[key].impl
            # TODO: store the history as (state, object) tuples
            # so we don't have to keep converting here
            history = impl.get_history(
                state,
                state.dict,
                passive
                | attributes.LOAD_AGAINST_COMMITTED
                | attributes.NO_RAISE,
            )
            if history and impl.uses_objects:
                state_history = history.as_state()
            else:
                state_history = history
            self.attributes[hashkey] = (history, state_history, passive)

        return state_history

    def has_dep(self, processor):
        return (processor, True) in self.presort_actions

    def register_preprocessor(self, processor, fromparent):
        key = (processor, fromparent)
        if key not in self.presort_actions:
            self.presort_actions[key] = Preprocess(processor, fromparent)

    def register_object(
        self,
        state: InstanceState[Any],
        isdelete: bool = False,
        listonly: bool = False,
        cancel_delete: bool = False,
        operation: Optional[str] = None,
        prop: Optional[MapperProperty] = None,
    ) -> bool:
        if not self.session._contains_state(state):
            # this condition is normal when objects are registered
            # as part of a relationship cascade operation.  it should
            # not occur for the top-level register from Session.flush().
            if not state.deleted and operation is not None:
                util.warn(
                    "Object of type %s not in session, %s operation "
                    "along '%s' will not proceed"
                    % (orm_util.state_class_str(state), operation, prop)
                )
            return False

        if state not in self.states:
            mapper = state.manager.mapper

            if mapper not in self.mappers:
                self._per_mapper_flush_actions(mapper)

            self.mappers[mapper].add(state)
            self.states[state] = (isdelete, listonly)
        else:
            if not listonly and (isdelete or cancel_delete):
                self.states[state] = (isdelete, False)
        return True

    def register_post_update(self, state, post_update_cols):
        mapper = state.manager.mapper.base_mapper
        states, cols = self.post_update_states[mapper]
        states.add(state)
        cols.update(post_update_cols)

    def _per_mapper_flush_actions(self, mapper):
        saves = SaveUpdateAll(self, mapper.base_mapper)
        deletes = DeleteAll(self, mapper.base_mapper)
        self.dependencies.add((saves, deletes))

        for dep in mapper._dependency_processors:
            dep.per_property_preprocessors(self)

        for prop in mapper.relationships:
            if prop.viewonly:
                continue
            dep = prop._dependency_processor
            dep.per_property_preprocessors(self)

    @util.memoized_property
    def _mapper_for_dep(self):
        """return a dynamic mapping of (Mapper, DependencyProcessor) to
        True or False, indicating if the DependencyProcessor operates
        on objects of that Mapper.

        The result is stored in the dictionary persistently once
        calculated.

        """
        return util.PopulateDict(
            lambda tup: tup[0]._props.get(tup[1].key) is tup[1].prop
        )

    def filter_states_for_dep(self, dep, states):
        """Filter the given list of InstanceStates to those relevant to the
        given DependencyProcessor.

        """
        mapper_for_dep = self._mapper_for_dep
        return [s for s in states if mapper_for_dep[(s.manager.mapper, dep)]]

    def states_for_mapper_hierarchy(self, mapper, isdelete, listonly):
        checktup = (isdelete, listonly)
        for mapper in mapper.base_mapper.self_and_descendants:
            for state in self.mappers[mapper]:
                if self.states[state] == checktup:
                    yield state

    def _generate_actions(self):
        """Generate the full, unsorted collection of PostSortRecs as
        well as dependency pairs for this UOWTransaction.

        """
        # execute presort_actions, until all states
        # have been processed.   a presort_action might
        # add new states to the uow.
        while True:
            ret = False
            for action in list(self.presort_actions.values()):
                if action.execute(self):
                    ret = True
            if not ret:
                break

        # see if the graph of mapper dependencies has cycles.
        self.cycles = cycles = topological.find_cycles(
            self.dependencies, list(self.postsort_actions.values())
        )

        if cycles:
            # if yes, break the per-mapper actions into
            # per-state actions
            convert = {
                rec: set(rec.per_state_flush_actions(self)) for rec in cycles
            }

            # rewrite the existing dependencies to point to
            # the per-state actions for those per-mapper actions
            # that were broken up.
            for edge in list(self.dependencies):
                if (
                    None in edge
                    or edge[0].disabled
                    or edge[1].disabled
                    or cycles.issuperset(edge)
                ):
                    self.dependencies.remove(edge)
                elif edge[0] in cycles:
                    self.dependencies.remove(edge)
                    for dep in convert[edge[0]]:
                        self.dependencies.add((dep, edge[1]))
                elif edge[1] in cycles:
                    self.dependencies.remove(edge)
                    for dep in convert[edge[1]]:
                        self.dependencies.add((edge[0], dep))

        return {
            a for a in self.postsort_actions.values() if not a.disabled
        }.difference(cycles)

    def execute(self) -> None:
        postsort_actions = self._generate_actions()

        postsort_actions = sorted(
            postsort_actions,
            key=lambda item: item.sort_key,
        )
        # sort = topological.sort(self.dependencies, postsort_actions)
        # print "--------------"
        # print "\ndependencies:", self.dependencies
        # print "\ncycles:", self.cycles
        # print "\nsort:", list(sort)
        # print "\nCOUNT OF POSTSORT ACTIONS", len(postsort_actions)

        # execute
        if self.cycles:
            for subset in topological.sort_as_subsets(
                self.dependencies, postsort_actions
            ):
                set_ = set(subset)
                while set_:
                    n = set_.pop()
                    n.execute_aggregate(self, set_)
        else:
            for rec in topological.sort(self.dependencies, postsort_actions):
                rec.execute(self)

    def finalize_flush_changes(self) -> None:
        """Mark processed objects as clean / deleted after a successful
        flush().

        This method is called within the flush() method after the
        execute() method has succeeded and the transaction has been committed.

        """
        if not self.states:
            return

        states = set(self.states)
        isdel = {
            s for (s, (isdelete, listonly)) in self.states.items() if isdelete
        }
        other = states.difference(isdel)
        if isdel:
            self.session._remove_newly_deleted(isdel)
        if other:
            self.session._register_persistent(other)


class IterateMappersMixin:
    __slots__ = ()

    def _mappers(self, uow):
        if self.fromparent:
            return iter(
                m
                for m in self.dependency_processor.parent.self_and_descendants
                if uow._mapper_for_dep[(m, self.dependency_processor)]
            )
        else:
            return self.dependency_processor.mapper.self_and_descendants


class Preprocess(IterateMappersMixin):
    __slots__ = (
        "dependency_processor",
        "fromparent",
        "processed",
        "setup_flush_actions",
    )

    def __init__(self, dependency_processor, fromparent):
        self.dependency_processor = dependency_processor
        self.fromparent = fromparent
        self.processed = set()
        self.setup_flush_actions = False

    def execute(self, uow):
        delete_states = set()
        save_states = set()

        for mapper in self._mappers(uow):
            for state in uow.mappers[mapper].difference(self.processed):
                (isdelete, listonly) = uow.states[state]
                if not listonly:
                    if isdelete:
                        delete_states.add(state)
                    else:
                        save_states.add(state)

        if delete_states:
            self.dependency_processor.presort_deletes(uow, delete_states)
            self.processed.update(delete_states)
        if save_states:
            self.dependency_processor.presort_saves(uow, save_states)
            self.processed.update(save_states)

        if delete_states or save_states:
            if not self.setup_flush_actions and (
                self.dependency_processor.prop_has_changes(
                    uow, delete_states, True
                )
                or self.dependency_processor.prop_has_changes(
                    uow, save_states, False
                )
            ):
                self.dependency_processor.per_property_flush_actions(uow)
                self.setup_flush_actions = True
            return True
        else:
            return False


class PostSortRec:
    __slots__ = ("disabled",)

    def __new__(cls, uow, *args):
        key = (cls,) + args
        if key in uow.postsort_actions:
            return uow.postsort_actions[key]
        else:
            uow.postsort_actions[key] = ret = object.__new__(cls)
            ret.disabled = False
            return ret

    def execute_aggregate(self, uow, recs):
        self.execute(uow)


class ProcessAll(IterateMappersMixin, PostSortRec):
    __slots__ = "dependency_processor", "isdelete", "fromparent", "sort_key"

    def __init__(self, uow, dependency_processor, isdelete, fromparent):
        self.dependency_processor = dependency_processor
        self.sort_key = (
            "ProcessAll",
            self.dependency_processor.sort_key,
            isdelete,
        )
        self.isdelete = isdelete
        self.fromparent = fromparent
        uow.deps[dependency_processor.parent.base_mapper].add(
            dependency_processor
        )

    def execute(self, uow):
        states = self._elements(uow)
        if self.isdelete:
            self.dependency_processor.process_deletes(uow, states)
        else:
            self.dependency_processor.process_saves(uow, states)

    def per_state_flush_actions(self, uow):
        # this is handled by SaveUpdateAll and DeleteAll,
        # since a ProcessAll should unconditionally be pulled
        # into per-state if either the parent/child mappers
        # are part of a cycle
        return iter([])

    def __repr__(self):
        return "%s(%s, isdelete=%s)" % (
            self.__class__.__name__,
            self.dependency_processor,
            self.isdelete,
        )

    def _elements(self, uow):
        for mapper in self._mappers(uow):
            for state in uow.mappers[mapper]:
                (isdelete, listonly) = uow.states[state]
                if isdelete == self.isdelete and not listonly:
                    yield state


class PostUpdateAll(PostSortRec):
    __slots__ = "mapper", "isdelete", "sort_key"

    def __init__(self, uow, mapper, isdelete):
        self.mapper = mapper
        self.isdelete = isdelete
        self.sort_key = ("PostUpdateAll", mapper._sort_key, isdelete)

    @util.preload_module("sqlalchemy.orm.persistence")
    def execute(self, uow):
        persistence = util.preloaded.orm_persistence
        states, cols = uow.post_update_states[self.mapper]
        states = [s for s in states if uow.states[s][0] == self.isdelete]

        persistence.post_update(self.mapper, states, uow, cols)


class SaveUpdateAll(PostSortRec):
    __slots__ = ("mapper", "sort_key")

    def __init__(self, uow, mapper):
        self.mapper = mapper
        self.sort_key = ("SaveUpdateAll", mapper._sort_key)
        assert mapper is mapper.base_mapper

    @util.preload_module("sqlalchemy.orm.persistence")
    def execute(self, uow):
        util.preloaded.orm_persistence.save_obj(
            self.mapper,
            uow.states_for_mapper_hierarchy(self.mapper, False, False),
            uow,
        )

    def per_state_flush_actions(self, uow):
        states = list(
            uow.states_for_mapper_hierarchy(self.mapper, False, False)
        )
        base_mapper = self.mapper.base_mapper
        delete_all = DeleteAll(uow, base_mapper)
        for state in states:
            # keep saves before deletes -
            # this ensures 'row switch' operations work
            action = SaveUpdateState(uow, state)
            uow.dependencies.add((action, delete_all))
            yield action

        for dep in uow.deps[self.mapper]:
            states_for_prop = uow.filter_states_for_dep(dep, states)
            dep.per_state_flush_actions(uow, states_for_prop, False)

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, self.mapper)


class DeleteAll(PostSortRec):
    __slots__ = ("mapper", "sort_key")

    def __init__(self, uow, mapper):
        self.mapper = mapper
        self.sort_key = ("DeleteAll", mapper._sort_key)
        assert mapper is mapper.base_mapper

    @util.preload_module("sqlalchemy.orm.persistence")
    def execute(self, uow):
        util.preloaded.orm_persistence.delete_obj(
            self.mapper,
            uow.states_for_mapper_hierarchy(self.mapper, True, False),
            uow,
        )

    def per_state_flush_actions(self, uow):
        states = list(
            uow.states_for_mapper_hierarchy(self.mapper, True, False)
        )
        base_mapper = self.mapper.base_mapper
        save_all = SaveUpdateAll(uow, base_mapper)
        for state in states:
            # keep saves before deletes -
            # this ensures 'row switch' operations work
            action = DeleteState(uow, state)
            uow.dependencies.add((save_all, action))
            yield action

        for dep in uow.deps[self.mapper]:
            states_for_prop = uow.filter_states_for_dep(dep, states)
            dep.per_state_flush_actions(uow, states_for_prop, True)

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, self.mapper)


class ProcessState(PostSortRec):
    __slots__ = "dependency_processor", "isdelete", "state", "sort_key"

    def __init__(self, uow, dependency_processor, isdelete, state):
        self.dependency_processor = dependency_processor
        self.sort_key = ("ProcessState", dependency_processor.sort_key)
        self.isdelete = isdelete
        self.state = state

    def execute_aggregate(self, uow, recs):
        cls_ = self.__class__
        dependency_processor = self.dependency_processor
        isdelete = self.isdelete
        our_recs = [
            r
            for r in recs
            if r.__class__ is cls_
            and r.dependency_processor is dependency_processor
            and r.isdelete is isdelete
        ]
        recs.difference_update(our_recs)
        states = [self.state] + [r.state for r in our_recs]
        if isdelete:
            dependency_processor.process_deletes(uow, states)
        else:
            dependency_processor.process_saves(uow, states)

    def __repr__(self):
        return "%s(%s, %s, delete=%s)" % (
            self.__class__.__name__,
            self.dependency_processor,
            orm_util.state_str(self.state),
            self.isdelete,
        )


class SaveUpdateState(PostSortRec):
    __slots__ = "state", "mapper", "sort_key"

    def __init__(self, uow, state):
        self.state = state
        self.mapper = state.mapper.base_mapper
        self.sort_key = ("ProcessState", self.mapper._sort_key)

    @util.preload_module("sqlalchemy.orm.persistence")
    def execute_aggregate(self, uow, recs):
        persistence = util.preloaded.orm_persistence
        cls_ = self.__class__
        mapper = self.mapper
        our_recs = [
            r for r in recs if r.__class__ is cls_ and r.mapper is mapper
        ]
        recs.difference_update(our_recs)
        persistence.save_obj(
            mapper, [self.state] + [r.state for r in our_recs], uow
        )

    def __repr__(self):
        return "%s(%s)" % (
            self.__class__.__name__,
            orm_util.state_str(self.state),
        )


class DeleteState(PostSortRec):
    __slots__ = "state", "mapper", "sort_key"

    def __init__(self, uow, state):
        self.state = state
        self.mapper = state.mapper.base_mapper
        self.sort_key = ("DeleteState", self.mapper._sort_key)

    @util.preload_module("sqlalchemy.orm.persistence")
    def execute_aggregate(self, uow, recs):
        persistence = util.preloaded.orm_persistence
        cls_ = self.__class__
        mapper = self.mapper
        our_recs = [
            r for r in recs if r.__class__ is cls_ and r.mapper is mapper
        ]
        recs.difference_update(our_recs)
        states = [self.state] + [r.state for r in our_recs]
        persistence.delete_obj(
            mapper, [s for s in states if uow.states[s][0]], uow
        )

    def __repr__(self):
        return "%s(%s)" % (
            self.__class__.__name__,
            orm_util.state_str(self.state),
        )
