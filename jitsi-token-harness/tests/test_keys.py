"""The ASAP key server filename is the thing people get wrong. Pin it down."""

from __future__ import annotations

import hashlib
import stat

import pytest

from jitsi_phase1.keys import generate_keypair, keyfile_name


def test_filename_is_sha256_of_the_kid_not_the_kid():
    kid = "zulip-jitsi-2026-07"
    expected = hashlib.sha256(kid.encode()).hexdigest() + ".pem"
    assert keyfile_name(kid) == expected
    assert kid not in keyfile_name(kid)


def test_filename_is_stable_across_calls():
    assert keyfile_name("a") == keyfile_name("a")
    assert keyfile_name("a") != keyfile_name("b")


def test_empty_kid_is_refused():
    with pytest.raises(ValueError):
        keyfile_name("")


def test_generate_lays_out_both_halves(tmp_path):
    pair = generate_keypair("zulip-jitsi-2026-07", tmp_path / "secrets", tmp_path / "keyserver")

    assert pair.private_key_path.name == "zulip-jitsi-2026-07.key"
    assert pair.public_key_path.name == keyfile_name("zulip-jitsi-2026-07")
    assert pair.private_key_path.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
    assert pair.public_key_path.read_bytes().startswith(b"-----BEGIN PUBLIC KEY-----")


def test_private_key_is_not_world_readable(tmp_path):
    pair = generate_keypair("k", tmp_path / "secrets", tmp_path / "keyserver")
    mode = stat.S_IMODE(pair.private_key_path.stat().st_mode)
    assert mode == 0o600


def test_refuses_to_clobber_an_existing_key(tmp_path):
    generate_keypair("k", tmp_path / "secrets", tmp_path / "keyserver")
    with pytest.raises(FileExistsError, match="new kid"):
        generate_keypair("k", tmp_path / "secrets", tmp_path / "keyserver")


def test_overwrite_is_available_when_asked_for(tmp_path):
    first = generate_keypair("k", tmp_path / "secrets", tmp_path / "keyserver")
    second = generate_keypair(
        "k", tmp_path / "secrets", tmp_path / "keyserver", overwrite=True
    )
    assert first.private_key_pem != second.private_key_pem
