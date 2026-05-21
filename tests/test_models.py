from src.models import VideoState, VideoEntry, FolderEntry


def test_video_entry_defaults():
    v = VideoEntry(
        panda_id="abc",
        panda_folder="EDUCACIONAL | Test",
        title="Aula 01",
    )
    assert v.state == VideoState.PENDING
    assert v.retry_count == 0
    assert v.sp_media_code is None
    assert v.last_error is None


def test_video_state_transitions_listed():
    assert VideoState.PENDING.value == "pending"
    assert VideoState.DONE.value == "done"
    assert VideoState.FAILED.value == "failed"


def test_folder_entry_minimal():
    f = FolderEntry(panda_folder_id="uuid-1", sp_folder_code=None)
    assert f.panda_folder_id == "uuid-1"
    assert f.sp_folder_code is None
