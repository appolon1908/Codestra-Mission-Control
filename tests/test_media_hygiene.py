from pathlib import Path

import mission_control.media_hygiene as mh


def media(path: str, size: int, kind: str = "video", sha: str | None = None) -> mh.MediaFile:
    return mh.MediaFile(Path(path), size, 1_700_000_000.0, kind, sha)


def test_typescript_ts_is_never_media():
    assert mh.classify_media(Path(r"C:\Users\Usuario\Videos\clip.ts")) is None


def test_source_and_system_trees_are_excluded():
    assert mh.classify_media(Path(r"C:\Users\agent\Documents\GitHub\repo\demo.mp4")) is None
    assert mh.classify_media(Path(r"C:\Codestra-Development-Hub\assets\demo.mp4")) is None
    assert mh.classify_media(Path(r"C:\repo\node_modules\fixture.mp4")) is None
    assert mh.classify_media(Path(r"C:\Windows\Temp\demo.mp4")) is None


def test_exact_duplicates_require_same_sha(monkeypatch):
    a = media(r"C:\Users\Usuario\Videos\a.mp4", 100)
    b = media(r"C:\Users\Usuario\Downloads\b.mp4", 100)
    c = media(r"C:\Users\Usuario\Videos\c.mp4", 100)

    hashes = {a.path: "aaa", b.path: "aaa", c.path: "bbb"}
    monkeypatch.setattr(mh, "sha256_file", lambda path: hashes[path])

    groups = mh.exact_duplicate_groups([a, b, c])
    assert len(groups) == 1
    assert groups[0].canonical.path == a.path
    assert [item.path for item in groups[0].redundant] == [b.path]


def test_archive_plan_only_contains_unique_large_c_drive_video():
    threshold = mh.DEFAULT_ARCHIVE_THRESHOLD_BYTES
    unique = media(r"C:\Users\Usuario\Videos\unique.mp4", threshold + 1)
    duplicate = media(r"C:\Users\Usuario\Downloads\copy.mp4", threshold + 1, sha="same")
    canonical = media(r"C:\Users\Usuario\Videos\original.mp4", threshold + 1, sha="same")
    group = mh.DuplicateGroup("same", canonical, (duplicate,))

    plan = mh.plan_archive(
        [unique, canonical, duplicate],
        [group],
        Path(r"E:\Media Library\Videos"),
    )

    sources = {src for src, _ in plan}
    assert unique.path in sources
    assert canonical.path in sources
    assert duplicate.path not in sources
    assert all(dst.name in {"unique.mp4", "original.mp4"} for _, dst in plan)


def test_unique_media_is_never_marked_redundant():
    unique = media(r"C:\Users\Usuario\Videos\unique.mp4", 123, sha="u")
    assert mh.redundant_paths([]) == set()
    assert unique.path not in mh.redundant_paths([])


def test_overlapping_scan_roots_never_create_self_duplicates(tmp_path):
    video = tmp_path / "Videos" / "only.mp4"
    video.parent.mkdir()
    video.write_bytes(b"unique-video-data")

    found = mh.scan_media([tmp_path, video.parent, tmp_path])
    assert len(found) == 1
    assert found[0].path == video
    assert mh.exact_duplicate_groups(found + found) == []
    assert video not in mh.redundant_paths(mh.exact_duplicate_groups(found + found))


def test_direct_duplicate_alias_is_not_its_own_redundant_copy():
    original = media(r"C:\Users\Usuario\Videos\clip.mp4", 100, sha="hash")
    alias = media(r"c:\users\usuario\videos\clip.mp4", 100, sha="hash")
    groups = mh.exact_duplicate_groups([original, alias, original])
    assert groups == []


def test_archive_plan_disambiguates_same_name_and_month():
    threshold = mh.DEFAULT_ARCHIVE_THRESHOLD_BYTES
    a = media(r"C:\Users\Usuario\Videos\clip.mp4", threshold + 1)
    b = media(r"C:\Users\Usuario\Desktop\clip.mp4", threshold + 1)
    archive_root = Path(r"E:\Media Library\Videos")
    plan = mh.plan_archive([a, b], [], archive_root)
    assert {source for source, _ in plan} == {a.path, b.path}
    assert len({str(target).casefold() for _, target in plan}) == 2
    assert all(target.suffix == ".mp4" for _, target in plan)
    assert mh.plan_archive([b, a], [], archive_root) == plan


def test_archive_plan_will_not_overwrite_preexisting_target(tmp_path):
    threshold = mh.DEFAULT_ARCHIVE_THRESHOLD_BYTES
    video = media(r"C:\Users\Usuario\Videos\clip.mp4", threshold + 1)
    preferred = mh.archive_target(video, tmp_path)
    assert preferred is not None
    preferred.parent.mkdir(parents=True)
    preferred.write_bytes(b"existing-unique-video")

    plan = mh.plan_archive([video], [], tmp_path)
    assert len(plan) == 1
    assert plan[0][0] == video.path
    assert plan[0][1] != preferred
    assert not plan[0][1].exists()
    assert preferred.read_bytes() == b"existing-unique-video"
