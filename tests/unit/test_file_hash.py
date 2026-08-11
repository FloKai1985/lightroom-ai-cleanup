from pathlib import Path

from lr_cleanup.analysis.file_hash import sha256_file


def test_identical_bytes_produce_identical_hash(tmp_path: Path) -> None:
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"identical content" * 100)
    b.write_bytes(b"identical content" * 100)

    assert sha256_file(a) == sha256_file(b)


def test_different_bytes_produce_different_hash(tmp_path: Path) -> None:
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"content A")
    b.write_bytes(b"content B")

    assert sha256_file(a) != sha256_file(b)


def test_hash_is_stable_across_calls(tmp_path: Path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(b"some file content")

    assert sha256_file(path) == sha256_file(path)


def test_hash_is_64_char_lowercase_hex(tmp_path: Path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(b"x")

    digest = sha256_file(path)
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)  # raises if not valid hex
