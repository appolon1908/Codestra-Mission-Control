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
