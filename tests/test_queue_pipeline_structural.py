from sari.core.queue_pipeline import (
    CoalesceTask,
    FsEvent,
    FsEventKind,
    TaskAction,
    split_moved_event,
)


def test_split_moved_event_returns_coalesce_tasks_not_tuples():
    event = FsEvent(
        kind=FsEventKind.MOVED,
        path="root/old.py",
        dest_path="root/new.py",
    )

    actions = split_moved_event(event)

    assert len(actions) == 2
    assert all(isinstance(item, CoalesceTask) for item in actions)
    assert actions[0].action == TaskAction.DELETE
    assert actions[0].path == "root/old.py"
    assert actions[1].action == TaskAction.INDEX
    assert actions[1].path == "root/new.py"

