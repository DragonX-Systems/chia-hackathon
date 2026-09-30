"""Catalog wiring without requiring Java or a RISC-V compiler in unit tests."""
import json
import sys
from pathlib import Path

from experiments.coverage_growth import prepare_mediumboom_crv_categories as prepare
from adapters.mediumboom.crv_profiles import PROFILE_PATH, load_profiles
from chialoop.core.catalog import JobCatalog
from chialoop.core.config import CampaignSpec
from test_crv_profiles import HARNESS, elf_fixture


def test_prepared_catalog_binds_nodes_seeds_signatures_and_simulator(tmp_path, monkeypatch):
    study = tmp_path/'study'
    study.mkdir()
    (study/'coverage_points.json').write_text(json.dumps({'points': [{'point_id':'bin-a','partition_id':'p'}]}))
    (study/'monitor_modules.json').write_text('[]')
    chipyard = tmp_path/'chipyard'
    env = chipyard/'tools/torture/env/p'
    env.mkdir(parents=True)
    for name in ('riscv_test.h','link.ld'):
        (env/name).write_text('fixture')
    simulator = tmp_path/'sim'
    simulator.write_text('simulator fixture')
    out = tmp_path/'out'

    def generate(source, output, plans, args):
        for plan in plans:
            (output/f"{plan['job_id']}.S").write_text(HARNESS.replace('17', str(plan['seed'])))
        return {'generator_commit':'fixture', 'overlay_sha256':'fixture'}

    def compile_fixture(cmd, **kwargs):
        elf_fixture(Path(cmd[cmd.index('-o')+1]), Path(cmd[-1]).read_bytes())

    monkeypatch.setattr(prepare, 'generate_sources', generate)
    monkeypatch.setattr(prepare, 'reference_signature', lambda *args: 'spike-reference-fixture')
    monkeypatch.setattr(prepare.subprocess, 'run', compile_fixture)
    monkeypatch.setattr(prepare.subprocess, 'check_output', lambda *a, **kw: '13.2.0')
    monkeypatch.setattr(sys, 'argv', ['prepare', '--study',str(study),'--out',str(out),
        '--chipyard',str(chipyard),'--simulator',str(simulator), '--riscv-gcc',sys.executable,
        '--spike',sys.executable,'--per-node','2','--seed-base','1000','--loop-count','1'])
    prepare.main()
    campaign = CampaignSpec.read_json(out/'campaign.json')
    catalog = JobCatalog.read_jsonl(out/'catalog.jsonl')
    assert (campaign.n1,campaign.n2,campaign.n3) == (5,0,0)
    assert len(catalog.jobs) == 10
    assert len({j.payload['seed'] for j in catalog.jobs}) == 10
    assert len({j.payload['stimulus_sha256'] for j in catalog.jobs}) == 10
    for index in range(1,6):
        jobs = [j for j in catalog.jobs if j.node_id == f'crv-{index}']
        assert len(jobs) == 2
        for job in jobs:
            assert job.payload['crv_profile'] == job.node_id
            assert job.payload['simulator_sha256'] == prepare.sha256(simulator)
            assert job.payload['expected_signature_sha256'] == 'spike-reference-fixture'
            assert job.seed_or_query_id == f"crv-seed:{job.payload['seed']}"
    manifest = json.loads((out/'generator_manifest.json').read_text())
    assert manifest['source_config_sha256'] == {
        node: prepare.sha256(PROFILE_PATH.parent / profile['config'])
        for node, profile in load_profiles().items()
    }
    assert all(c['torture.generator.loop_size']=='0' for c in manifest['effective_configs'].values())
    assert {catalog.job(j).node_id for j in catalog.static_manifest()[:5]} == {f'crv-{i}' for i in range(1,6)}
