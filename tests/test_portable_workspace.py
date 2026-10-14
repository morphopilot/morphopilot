"""Public source starts empty and never depends on a research filesystem."""
import ast
from pathlib import Path

from test_large_volumes import api


def test_legacy_demo_flag_cannot_load_research_data(api, monkeypatch):
    client, module = api
    (module.ROOT / 'initialized.json').unlink()
    monkeypatch.setenv('MORPHOPILOT_BOOTSTRAP_DEMO', 'true')
    module.prepare()
    state = client.get('/state').json()
    assert state['samples'] == []
    assert state['active_model'] == 'official'
    assert client.get('/demos').json()['demos'] == []


def test_official_model_default_is_repo_local_and_supports_explicit_override(monkeypatch, tmp_path):
    # Evaluate the actual path assignment without importing GPU dependencies.
    source = Path(__file__).resolve().parents[1] / 'backend/model_build.py'
    tree = ast.parse(source.read_text())
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'OFFICIAL' for target in node.targets))
    import os
    namespace = {'Path': Path, 'os': os, '__file__': str(source)}
    monkeypatch.delenv('MORPHOPILOT_MODEL_DIR', raising=False)
    compiled = compile(ast.Module(body=[assignment], type_ignores=[]), str(source), 'exec')
    exec(compiled, namespace)
    assert namespace['OFFICIAL'] == source.parents[1] / 'models/nnInteractive_v1.0'
    custom = tmp_path / 'chosen model'
    monkeypatch.setenv('MORPHOPILOT_MODEL_DIR', str(custom))
    exec(compiled, namespace)
    assert namespace['OFFICIAL'] == custom
