"""Filesystem attack, crash, bounds, and idempotence evidence for Slice C1."""

from __future__ import annotations

import os
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from tradesieve.adapters.local_raw_object_store import LocalImmutableRawObjectStore
from tradesieve.domain.source_snapshot import (
    MAX_RAW_OBJECT_BYTES,
    ArtifactWriteOutcome,
    RawObjectIntegrityError,
    RawObjectMetadata,
    RawObjectRef,
)
from tradesieve.ports.source_snapshot import SourceSnapshotPersistenceError

NOW = datetime(2026, 8, 7, 1, tzinfo=UTC)


def private_root(tmp_path: Path, name: str = "raw-private") -> Path:
    root = tmp_path / name
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def metadata_for(
    content: bytes = b'{"synthetic":"raw"}',
    *,
    original_name: str = "never-use-this-name.json",
    source_id: str = "source-never-use-this-path",
) -> RawObjectMetadata:
    return RawObjectMetadata.from_bytes(
        deployment_id="demo-deployment",
        source_id=source_id,
        original_name=original_name,
        media_type="application/json",
        charset="utf-8",
        retrieved_at=NOW,
        effective_from=None,
        content=content,
    )


def object_file(root: Path) -> Path:
    values = tuple(root.rglob("*.raw"))
    assert len(values) == 1
    return values[0]


def temporary_files(root: Path) -> tuple[Path, ...]:
    return tuple(path for path in root.rglob("*.tmp-*") if path.is_file())


def remove_object_file(root: Path) -> Path:
    leaf = object_file(root)
    leaf.unlink()
    return leaf


def test_put_get_modes_private_paths_idempotence_and_public_surface(
    tmp_path: Path,
) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    content = b'{"synthetic":"raw"}'
    metadata = metadata_for(content)

    assert store.put_exact(metadata, content) is ArtifactWriteOutcome.APPLIED
    leaf = object_file(root)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(parent.stat().st_mode) == 0o700
        for parent in leaf.parents
        if parent == root or root in parent.parents
    )
    assert stat.S_ISREG(leaf.stat().st_mode)
    assert stat.S_IMODE(leaf.stat().st_mode) == 0o600
    assert metadata.original_name not in str(leaf)
    assert metadata.source_id not in str(leaf)
    assert metadata.object_id in leaf.name
    assert metadata.content_hash.removeprefix("sha256:") in str(leaf)
    assert temporary_files(root) == ()
    assert store.get_verified(metadata.reference()) == content
    assert store.put_exact(metadata, content) is ArtifactWriteOutcome.IDEMPOTENT
    assert store.get_verified(metadata.reference()) == content

    for forbidden in ("delete", "list", "enumerate", "export", "path"):
        assert not hasattr(store, forbidden)


def test_empty_and_maximum_bounded_objects_round_trip(tmp_path: Path) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    empty = metadata_for(b"", original_name="empty.json")
    maximum_content = b"x" * MAX_RAW_OBJECT_BYTES
    maximum = metadata_for(maximum_content, original_name="maximum.json")

    assert store.put_exact(empty, b"") is ArtifactWriteOutcome.APPLIED
    assert store.get_verified(empty.reference()) == b""
    assert store.put_exact(maximum, maximum_content) is ArtifactWriteOutcome.APPLIED
    assert store.get_verified(maximum.reference()) == maximum_content


def test_put_validates_typed_metadata_and_exact_bytes_before_filesystem(
    tmp_path: Path,
) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    metadata = metadata_for()

    with pytest.raises(RawObjectIntegrityError):
        store.put_exact(metadata, b"wrong")
    with pytest.raises(TypeError):
        store.put_exact(cast(RawObjectMetadata, object()), b"wrong")
    assert tuple(root.iterdir()) == ()


@pytest.mark.parametrize("invalid", ["relative", ".", "..", b"bytes", object()])
def test_constructor_rejects_invalid_root_configuration(
    tmp_path: Path, invalid: object
) -> None:
    del tmp_path
    with pytest.raises(ValueError):
        LocalImmutableRawObjectStore(cast(Any, invalid))


def test_constructor_rejects_missing_file_public_and_root_paths(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(SourceSnapshotPersistenceError):
        LocalImmutableRawObjectStore(missing)

    file_root = tmp_path / "file-root"
    file_root.write_bytes(b"not a directory")
    file_root.chmod(0o600)
    with pytest.raises(SourceSnapshotPersistenceError):
        LocalImmutableRawObjectStore(file_root)

    public = private_root(tmp_path, "public-root")
    public.chmod(0o755)
    with pytest.raises(SourceSnapshotPersistenceError):
        LocalImmutableRawObjectStore(public)

    with pytest.raises(ValueError):
        LocalImmutableRawObjectStore(Path("/"))
    with pytest.raises(ValueError):
        LocalImmutableRawObjectStore(tmp_path / ".." / "escaped-root")


def test_constructor_rejects_root_and_ancestor_symlinks(tmp_path: Path) -> None:
    target = private_root(tmp_path, "target")
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target, target_is_directory=True)
    with pytest.raises(SourceSnapshotPersistenceError):
        LocalImmutableRawObjectStore(root_link)

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir(mode=0o700)
    real_parent.chmod(0o700)
    nested = private_root(real_parent, "nested")
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(SourceSnapshotPersistenceError):
        LocalImmutableRawObjectStore(parent_link / nested.name)


def test_missing_wrong_size_wrong_hash_and_untyped_reads_fail_closed(
    tmp_path: Path,
) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    metadata = metadata_for()

    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(metadata.reference())
    with pytest.raises(ValueError):
        store.get_verified(cast(RawObjectRef, object()))

    store.put_exact(metadata, b'{"synthetic":"raw"}')
    leaf = object_file(root)
    leaf.write_bytes(b"short")
    leaf.chmod(0o600)
    assert (
        store.put_exact(metadata, b'{"synthetic":"raw"}')
        is ArtifactWriteOutcome.CONFLICT
    )
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(metadata.reference())

    leaf.write_bytes(b"z" * metadata.byte_length)
    leaf.chmod(0o600)
    assert (
        store.put_exact(metadata, b'{"synthetic":"raw"}')
        is ArtifactWriteOutcome.CONFLICT
    )
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(metadata.reference())


def test_private_file_mode_symlink_and_nonregular_leaf_fail_closed(
    tmp_path: Path,
) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    metadata = metadata_for()
    content = b'{"synthetic":"raw"}'
    store.put_exact(metadata, content)
    leaf = object_file(root)

    leaf.chmod(0o644)
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(metadata.reference())
    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(metadata, content)

    leaf.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(content)
    outside.chmod(0o600)
    leaf.symlink_to(outside)
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(metadata.reference())
    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(metadata, content)
    assert outside.read_bytes() == content

    leaf.unlink()
    leaf.mkdir(mode=0o700)
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(metadata.reference())
    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(metadata, content)


def test_private_directory_tampering_and_symlink_traversal_fail_closed(
    tmp_path: Path,
) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    metadata = metadata_for()
    content = b'{"synthetic":"raw"}'
    store.put_exact(metadata, content)
    leaf = object_file(root)
    digest_directory = leaf.parent

    digest_directory.chmod(0o755)
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(metadata.reference())
    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(metadata, content)
    digest_directory.chmod(0o700)

    moved = tmp_path / "moved-digest"
    digest_directory.rename(moved)
    digest_directory.symlink_to(moved, target_is_directory=True)
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(metadata.reference())
    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(metadata, content)


def test_root_replacement_after_construction_fails_closed(tmp_path: Path) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    metadata = metadata_for()
    moved = tmp_path / "moved-root"
    root.rename(moved)
    root.mkdir(mode=0o700)
    root.chmod(0o700)

    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(metadata, b'{"synthetic":"raw"}')
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(metadata.reference())
    assert tuple(root.iterdir()) == ()


def test_short_write_permission_and_fsync_failures_cleanup_only_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b'{"synthetic":"raw"}'
    metadata = metadata_for(content)

    short_root = private_root(tmp_path, "short-root")
    short_store = LocalImmutableRawObjectStore(short_root)
    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.write",
        lambda descriptor, value: 1,
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        short_store.put_exact(metadata, content)
    assert tuple(short_root.rglob("*.raw")) == ()
    assert temporary_files(short_root) == ()
    monkeypatch.undo()

    permission_root = private_root(tmp_path, "permission-root")
    permission_store = LocalImmutableRawObjectStore(permission_root)
    real_write = os.write

    def denied_write(descriptor: int, value: bytes) -> int:
        del descriptor, value
        raise PermissionError

    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.write", denied_write
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        permission_store.put_exact(metadata, content)
    assert tuple(permission_root.rglob("*.raw")) == ()
    assert temporary_files(permission_root) == ()
    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.write", real_write
    )

    fsync_root = private_root(tmp_path, "fsync-root")
    fsync_store = LocalImmutableRawObjectStore(fsync_root)
    real_fsync = os.fsync
    calls = 0

    def fail_content_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.fsync", fail_content_fsync
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        fsync_store.put_exact(metadata, content)
    assert tuple(fsync_root.rglob("*.raw")) == ()
    assert temporary_files(fsync_root) == ()


def test_reported_complete_write_without_bytes_fails_size_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    content = b'{"synthetic":"raw"}'
    metadata = metadata_for(content)
    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.write",
        lambda descriptor, value: len(value),
    )

    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(metadata, content)
    assert tuple(root.rglob("*.raw")) == ()
    assert temporary_files(root) == ()


def test_publish_never_overwrites_and_handles_exact_or_conflicting_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b'{"synthetic":"raw"}'
    metadata = metadata_for(content)
    real_link = os.link

    exact_root = private_root(tmp_path, "exact-race")
    exact_store = LocalImmutableRawObjectStore(exact_root)

    def exact_race(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
        follow_symlinks: bool,
    ) -> None:
        real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )
        raise FileExistsError

    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.link", exact_race
    )
    assert exact_store.put_exact(metadata, content) is ArtifactWriteOutcome.IDEMPOTENT
    assert exact_store.get_verified(metadata.reference()) == content
    assert temporary_files(exact_root) == ()
    monkeypatch.undo()

    conflict_root = private_root(tmp_path, "conflict-race")
    conflict_store = LocalImmutableRawObjectStore(conflict_root)

    def conflicting_race(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
        follow_symlinks: bool,
    ) -> None:
        del source, src_dir_fd, follow_symlinks
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=dst_dir_fd,
        )
        try:
            os.write(descriptor, b"z" * len(content))
        finally:
            os.close(descriptor)
        raise FileExistsError

    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.link", conflicting_race
    )
    assert conflict_store.put_exact(metadata, content) is ArtifactWriteOutcome.CONFLICT
    assert object_file(conflict_root).read_bytes() == b"z" * len(content)
    assert temporary_files(conflict_root) == ()


def test_disappearing_publish_target_and_link_failure_cleanup_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b'{"synthetic":"raw"}'
    metadata = metadata_for(content)

    missing_root = private_root(tmp_path, "missing-race")
    missing_store = LocalImmutableRawObjectStore(missing_root)

    def vanished_target(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise FileExistsError

    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.link", vanished_target
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        missing_store.put_exact(metadata, content)
    assert tuple(missing_root.rglob("*.raw")) == ()
    assert temporary_files(missing_root) == ()
    monkeypatch.undo()

    denied_root = private_root(tmp_path, "denied-link")
    denied_store = LocalImmutableRawObjectStore(denied_root)

    def denied_link(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise PermissionError

    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.link", denied_link
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        denied_store.put_exact(metadata, content)
    assert tuple(denied_root.rglob("*.raw")) == ()
    assert temporary_files(denied_root) == ()


def test_directory_fsync_failure_after_publish_preserves_final_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    content = b'{"synthetic":"raw"}'
    metadata = metadata_for(content)
    real_fsync = os.fsync
    call = 0

    def fail_final_directory_fsync(descriptor: int) -> None:
        nonlocal call
        call += 1
        # Three derived directories, file content, then final directory publish.
        if call == 5:
            raise OSError("synthetic directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.fsync",
        fail_final_directory_fsync,
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(metadata, content)
    assert object_file(root).read_bytes() == content
    assert temporary_files(root) == ()
    monkeypatch.undo()
    assert store.put_exact(metadata, content) is ArtifactWriteOutcome.IDEMPOTENT


def test_short_reads_and_read_permissions_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    content = b'{"synthetic":"raw"}'
    metadata = metadata_for(content)
    store.put_exact(metadata, content)
    real_read = os.read

    def short_read(descriptor: int, size: int) -> bytes:
        return real_read(descriptor, max(0, size - 2))

    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.read", short_read
    )
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(metadata.reference())
    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(metadata, content)
    monkeypatch.undo()

    leaf = object_file(root)
    real_open = os.open

    def denied_leaf_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if path == leaf.name:
            raise PermissionError
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.open", denied_leaf_open
    )
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(metadata.reference())
    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(metadata, content)


def test_same_size_file_change_during_read_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    content = b'{"synthetic":"raw"}'
    metadata = metadata_for(content)
    store.put_exact(metadata, content)
    leaf = object_file(root)
    real_read = os.read
    mutated = False

    def mutate_time_after_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        result = real_read(descriptor, size)
        if not mutated:
            mutated = True
            current = leaf.stat()
            os.utime(
                leaf,
                ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000_000),
            )
        return result

    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.read", mutate_time_after_read
    )
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(metadata.reference())
    monkeypatch.undo()

    mutated = False
    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.read", mutate_time_after_read
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(metadata, content)


def test_absolute_path_descriptor_close_failure_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    reference = metadata_for().reference()
    real_close = os.close
    failed = False

    def fail_first_close(descriptor: int) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("synthetic close failure")
        real_close(descriptor)

    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.close", fail_first_close
    )
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(reference)


def test_existing_unowned_temp_collision_is_not_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    content = b'{"synthetic":"raw"}'
    metadata = metadata_for(content)
    store.put_exact(metadata, content)
    leaf = remove_object_file(root)
    token = "f" * 32
    collision = leaf.parent / f".{leaf.name}.tmp-{token}"
    collision.write_bytes(b"unowned")
    collision.chmod(0o600)
    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.secrets.token_hex",
        lambda size: token,
    )

    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(metadata, content)
    assert collision.read_bytes() == b"unowned"
    assert not leaf.exists()


def test_unpublished_temp_cleanup_failure_never_removes_final_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    content = b'{"synthetic":"raw"}'
    metadata = metadata_for(content)
    real_unlink = os.unlink
    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.write",
        lambda descriptor, value: 1,
    )

    def denied_cleanup(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        dir_fd: int | None = None,
    ) -> None:
        if ".tmp-" in os.fspath(path):
            raise PermissionError
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(
        "tradesieve.adapters.local_raw_object_store.os.unlink", denied_cleanup
    )
    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(metadata, content)
    assert tuple(root.rglob("*.raw")) == ()
    assert len(temporary_files(root)) == 1


def test_reconstructed_reference_rejects_mutation_before_path_use(
    tmp_path: Path,
) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    reference = metadata_for().reference()
    object.__setattr__(reference, "object_id", cast(Any, object()))

    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(reference)
    assert tuple(root.iterdir()) == ()


def test_non_directory_derived_component_fails_without_replacement(
    tmp_path: Path,
) -> None:
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    metadata = metadata_for()
    objects = root / "objects"
    objects.write_bytes(b"attacker-controlled")
    objects.chmod(0o600)

    with pytest.raises(SourceSnapshotPersistenceError):
        store.put_exact(metadata, b'{"synthetic":"raw"}')
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(metadata.reference())
    assert objects.read_bytes() == b"attacker-controlled"


def test_store_remains_immutable_under_concurrent_exact_writers(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    content = b'{"synthetic":"raw"}'
    metadata = metadata_for(content)

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = tuple(
            executor.map(lambda _: store.put_exact(metadata, content), range(32))
        )
    assert outcomes.count(ArtifactWriteOutcome.APPLIED) == 1
    assert outcomes.count(ArtifactWriteOutcome.IDEMPOTENT) == 31
    assert store.get_verified(metadata.reference()) == content
    assert temporary_files(root) == ()
    assert len(tuple(root.rglob("*.raw"))) == 1


def test_test_cleanup_is_scoped_to_the_temporary_root(tmp_path: Path) -> None:
    # Keep this test's own destructive cleanup explicit and confined; the production
    # adapter deliberately exposes no deletion API.
    root = private_root(tmp_path)
    store = LocalImmutableRawObjectStore(root)
    metadata = metadata_for()
    store.put_exact(metadata, b'{"synthetic":"raw"}')
    shutil.rmtree(root)
    with pytest.raises(RawObjectIntegrityError):
        store.get_verified(metadata.reference())
