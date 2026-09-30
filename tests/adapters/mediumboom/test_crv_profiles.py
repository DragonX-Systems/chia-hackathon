"""Configuration, trap-harness and executable identity regressions."""
import json
import shutil
import struct

import pytest

from adapters.mediumboom.crv_profiles import (
    PROFILE_PATH, PREFIX, add_privilege_checks, load_profiles, read_config, effective_config,
    signature_sha256, stimulus_sha256,
)
from chialoop.core.config import CRV_CATEGORIES

HARNESS = 'RVTEST_RV64U\nRVTEST_CODE_BEGIN\nli x1, 17\nRVTEST_CODE_END\n'


def test_profiles_match_nodes_and_specialize_real_generator_controls():
    profiles = load_profiles()
    configs = {}
    for index, label in enumerate(CRV_CATEGORIES, 1):
        node = f"crv-{index}"
        assert profiles[node]["name"] == label
        assert profiles[node]["config"].startswith("crv_profiles/")
        source = read_config(PROFILE_PATH.parent / profiles[node]["config"])
        configs[node] = effective_config(profiles[node])
        assert configs[node][PREFIX+'nseqs'] == source[PREFIX+'nseqs']
        assert configs[node][PREFIX+'memsize'] == source[PREFIX+'memsize']
        for key, value in profiles[node]["overrides"].items():
            assert configs[node][PREFIX+key] == value
    assert configs['crv-1'][PREFIX+'mul'] == 'false'
    assert configs['crv-1'][PREFIX+'amo'] == 'false'
    assert configs['crv-2'][PREFIX+'mix.xmem'] == '65'
    assert configs['crv-2'][PREFIX+'profile.fence_percent'] == '20'
    assert configs['crv-3'][PREFIX+'profile.muldiv_only'] == 'true'
    assert configs['crv-3'][PREFIX+'profile.atomic_percent'] == '85'
    assert configs['crv-4'][PREFIX+'profile.privilege_tests'] == '64'
    assert configs['crv-5'][PREFIX+'nseqs'] == '400'


def test_privilege_prelude_is_seeded_and_restores_harness():
    first = add_privilege_checks(HARNESS, 19, 64)
    assert first == add_privilege_checks(HARNESS, 19, 64)
    assert first != add_privilege_checks(HARNESS, 20, 64)
    assert 'RVTEST_RV64M' in first
    assert first.count('bne s8, s10, crv_fail') == 64
    for cause in (2, 3, 8, 11):
        assert f'li s6, {cause}\n' in first
    for instruction in ('csrr t4, mcause', 'csrr t4, mepc', 'bne t4, s5, crv_fail',
                        'csrrw t2, mscratch, t1', 'csrw mtvec, s4', 'crv_user_body:'):
        assert instruction in first
    assert first.endswith('li x1, 17\nRVTEST_CODE_END\n')
    assert add_privilege_checks(HARNESS, 19, 0) == HARNESS


@pytest.mark.parametrize('seed', [-1, 1 << 63])
def test_seed_bounds(seed):
    with pytest.raises(ValueError, match='Scala Long'):
        add_privilege_checks(HARNESS, seed, 4)


def test_duplicate_profiles_rejected(tmp_path):
    shutil.copytree(PROFILE_PATH.parent / 'crv_profiles', tmp_path / 'crv_profiles')
    profiles = load_profiles()
    profiles['crv-2'] = dict(profiles['crv-1'])
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps(profiles))
    with pytest.raises(ValueError, match='different generator configuration'):
        load_profiles(path)


def elf_fixture(path, content):
    header = bytearray(64)
    header[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<Q', header, 40, 64)
    struct.pack_into('<HH', header, 58, 64, 1)
    section = struct.pack('<IIQQQQIIQQ', 0, 1, 6, 0x80000000, 128, len(content), 0, 0, 4, 0)
    path.write_bytes(header + section + content)


def test_identity_ignores_nonloaded_labels_but_not_instructions(tmp_path):
    a, b = tmp_path/'a', tmp_path/'b'
    elf_fixture(a, b'code')
    b.write_bytes(a.read_bytes() + b'changed filename or seed comment')
    assert stimulus_sha256(a) == stimulus_sha256(b)
    elf_fixture(b, b'new!')
    assert stimulus_sha256(a) != stimulus_sha256(b)


def test_signature_normalization_and_validation(tmp_path):
    a, b = tmp_path/'a', tmp_path/'b'
    a.write_text('A'*32+'\n')
    b.write_text('a'*32+'\r\n')
    assert signature_sha256(a) == signature_sha256(b)
    b.write_text('')
    with pytest.raises(ValueError):
        signature_sha256(b)
