"""Private local-volume storage for immutable, content-verified raw objects."""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import TracebackType

from tradesieve.domain.source_snapshot import (
    ArtifactWriteOutcome,
    RawObjectIntegrityError,
    RawObjectMetadata,
    RawObjectRef,
    bytes_sha256,
    verify_raw_bytes,
)
from tradesieve.ports.source_snapshot import SourceSnapshotPersistenceError

_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_NOFOLLOW = os.O_NOFOLLOW
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW | os.O_CLOEXEC
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | os.O_CLOEXEC


class _UnsafeLocalObject(Exception):
    """Internal marker mapped to the stable persistence/integrity boundary."""


class _FileDescriptor:
    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor

    def __enter__(self) -> int:
        return self._descriptor

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        os.close(self._descriptor)


class LocalImmutableRawObjectStore:
    """Store exact raw bytes below one pre-existing private absolute root.

    The public surface intentionally matches ``ImmutableRawObjectStore`` only. Paths
    are an adapter implementation detail and are derived exclusively from the typed
    object reference.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        if not isinstance(root, (str, os.PathLike)) or isinstance(root, bytes):
            raise ValueError("local raw object root must be a filesystem path")
        candidate = Path(root)
        if (
            not candidate.is_absolute()
            or candidate == Path(candidate.anchor)
            or any(part in {"", ".", ".."} for part in candidate.parts[1:])
        ):
            raise ValueError("local raw object root must be an absolute private path")
        self._root = candidate
        try:
            with _FileDescriptor(self._open_absolute_directory()) as descriptor:
                root_stat = os.fstat(descriptor)
                self._require_private_directory(root_stat)
                self._root_identity = (root_stat.st_dev, root_stat.st_ino)
        except (OSError, _UnsafeLocalObject):
            raise SourceSnapshotPersistenceError from None

    def put_exact(
        self, metadata: RawObjectMetadata, content: bytes
    ) -> ArtifactWriteOutcome:
        verified = verify_raw_bytes(metadata, content)
        reference = metadata.reference()
        try:
            with self._object_directory(reference, create=True) as directory:
                existing = self._existing_outcome(directory, reference)
                if existing is not None:
                    return existing
                return self._publish(directory, reference, verified)
        except (OSError, _UnsafeLocalObject):
            raise SourceSnapshotPersistenceError from None

    def get_verified(self, reference: RawObjectRef) -> bytes:
        if not isinstance(reference, RawObjectRef):
            raise ValueError("raw object reference must be typed")
        try:
            with self._object_directory(reference, create=False) as directory:
                descriptor = os.open(
                    self._leaf_name(reference), self._read_flags(), dir_fd=directory
                )
                with _FileDescriptor(descriptor) as opened:
                    before = self._require_private_file(os.fstat(opened))
                    if before.st_size != reference.byte_length:
                        raise _UnsafeLocalObject
                    content = self._read_exact(opened, reference.byte_length)
                    after = self._require_private_file(os.fstat(opened))
                    if self._file_identity(before) != self._file_identity(after):
                        raise _UnsafeLocalObject
                    if bytes_sha256(content) != reference.content_hash:
                        raise _UnsafeLocalObject
                    return content
        except (OSError, _UnsafeLocalObject):
            raise RawObjectIntegrityError from None

    @contextmanager
    def _object_directory(
        self, reference: RawObjectRef, *, create: bool
    ) -> Iterator[int]:
        # Reconstructing the reference prevents forged subclasses/mutated values from
        # becoming path components.
        try:
            checked = RawObjectRef(
                reference.object_id, reference.content_hash, reference.byte_length
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise _UnsafeLocalObject from exc
        digest = checked.content_hash.removeprefix("sha256:")
        with ExitStack() as stack:
            root = stack.enter_context(_FileDescriptor(self._open_absolute_directory()))
            root_stat = os.fstat(root)
            self._require_private_directory(root_stat)
            if (root_stat.st_dev, root_stat.st_ino) != self._root_identity:
                raise _UnsafeLocalObject
            current = root
            for segment in ("objects", digest[:2], digest[2:4]):
                current = stack.enter_context(
                    _FileDescriptor(
                        self._open_private_directory(current, segment, create=create)
                    )
                )
            yield current

    def _open_absolute_directory(self) -> int:
        current = os.open(self._root.anchor, self._directory_flags())
        try:
            for segment in self._root.parts[1:]:
                following = os.open(segment, self._directory_flags(), dir_fd=current)
                try:
                    os.close(current)
                except OSError:
                    os.close(following)
                    raise
                current = following
            return current
        except BaseException:
            os.close(current)
            raise

    def _open_private_directory(
        self, parent: int, segment: str, *, create: bool
    ) -> int:
        created = False
        if create:
            try:
                os.mkdir(segment, _DIRECTORY_MODE, dir_fd=parent)
                created = True
            except FileExistsError:
                pass
        descriptor = os.open(segment, self._directory_flags(), dir_fd=parent)
        try:
            if created:
                os.fchmod(descriptor, _DIRECTORY_MODE)
            self._require_private_directory(os.fstat(descriptor))
            if created:
                os.fsync(parent)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _existing_outcome(
        self, directory: int, reference: RawObjectRef
    ) -> ArtifactWriteOutcome | None:
        try:
            descriptor = os.open(
                self._leaf_name(reference), self._read_flags(), dir_fd=directory
            )
        except FileNotFoundError:
            return None
        with _FileDescriptor(descriptor) as opened:
            before = self._require_private_file(os.fstat(opened))
            if before.st_size != reference.byte_length:
                return ArtifactWriteOutcome.CONFLICT
            content = self._read_exact(opened, reference.byte_length)
            after = self._require_private_file(os.fstat(opened))
            if self._file_identity(before) != self._file_identity(after):
                raise _UnsafeLocalObject
            return (
                ArtifactWriteOutcome.IDEMPOTENT
                if bytes_sha256(content) == reference.content_hash
                else ArtifactWriteOutcome.CONFLICT
            )

    def _publish(
        self, directory: int, reference: RawObjectRef, content: bytes
    ) -> ArtifactWriteOutcome:
        leaf = self._leaf_name(reference)
        temporary = f".{leaf}.tmp-{secrets.token_hex(16)}"
        temporary_exists = False
        try:
            descriptor = os.open(
                temporary,
                self._create_flags(),
                _FILE_MODE,
                dir_fd=directory,
            )
            temporary_exists = True
            with _FileDescriptor(descriptor) as opened:
                os.fchmod(opened, _FILE_MODE)
                self._require_private_file(os.fstat(opened))
                written = os.write(opened, content)
                if written != len(content):
                    raise _UnsafeLocalObject
                written_stat = self._require_private_file(os.fstat(opened))
                if written_stat.st_size != len(content):
                    raise _UnsafeLocalObject
                os.fsync(opened)
            try:
                os.link(
                    temporary,
                    leaf,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
            except FileExistsError:
                outcome = self._existing_outcome(directory, reference)
                if outcome is None:
                    raise _UnsafeLocalObject from None
            else:
                outcome = ArtifactWriteOutcome.APPLIED
            os.unlink(temporary, dir_fd=directory)
            temporary_exists = False
            os.fsync(directory)
            return outcome
        except BaseException:
            if temporary_exists:
                try:
                    os.unlink(temporary, dir_fd=directory)
                    os.fsync(directory)
                except (FileNotFoundError, OSError):
                    pass
            raise

    @staticmethod
    def _read_exact(descriptor: int, expected_size: int) -> bytes:
        content = os.read(descriptor, expected_size + 1)
        if len(content) != expected_size or os.read(descriptor, 1) != b"":
            raise _UnsafeLocalObject
        return content

    @staticmethod
    def _file_identity(value: os.stat_result) -> tuple[int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
        )

    @staticmethod
    def _require_private_directory(value: os.stat_result) -> None:
        if (
            not stat.S_ISDIR(value.st_mode)
            or stat.S_IMODE(value.st_mode) != _DIRECTORY_MODE
            or value.st_uid != os.geteuid()
        ):
            raise _UnsafeLocalObject

    @staticmethod
    def _require_private_file(value: os.stat_result) -> os.stat_result:
        if (
            not stat.S_ISREG(value.st_mode)
            or stat.S_IMODE(value.st_mode) != _FILE_MODE
            or value.st_uid != os.geteuid()
        ):
            raise _UnsafeLocalObject
        return value

    @staticmethod
    def _leaf_name(reference: RawObjectRef) -> str:
        digest = reference.content_hash.removeprefix("sha256:")
        return f"{reference.object_id}.{digest}.raw"

    @staticmethod
    def _directory_flags() -> int:
        return _DIRECTORY_FLAGS

    @staticmethod
    def _read_flags() -> int:
        return _READ_FLAGS

    @staticmethod
    def _create_flags() -> int:
        return _CREATE_FLAGS


__all__ = ["LocalImmutableRawObjectStore"]
