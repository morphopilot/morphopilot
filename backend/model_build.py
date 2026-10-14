"""Portable nnInteractive architecture and checkpoint loader.

The model architecture and original checkpoint mapping are preserved.
Set MORPHOPILOT_MODEL_DIR to use a separately downloaded official checkpoint.
"""
import json
import os
from pathlib import Path
import torch
from nnunetv2.utilities.plans_handling.plans_handler import PlansManager
from nnInteractive.trainer.nnInteractiveTrainer import nnInteractiveTrainer_stub

OFFICIAL = Path(os.environ.get('MORPHOPILOT_MODEL_DIR', str(Path(__file__).resolve().parents[1] / 'models/nnInteractive_v1.0'))).expanduser().resolve()


def build_model(channels=8, pretrained=True, device='cuda'):
    plans = json.loads((OFFICIAL / 'plans.json').read_text())
    pm = PlansManager(plans)
    cm = pm.get_configuration('3d_fullres_ps192_bs24')
    net = nnInteractiveTrainer_stub.build_network_architecture(pm, cm, channels, 2, False)
    changed = []
    if pretrained:
        ck = torch.load(OFFICIAL / 'fold_0/checkpoint_final.pth', map_location='cpu', weights_only=False)
        src = ck['network_weights']
        dst = net.state_dict()
        for k, v in dst.items():
            old = src[k]
            if v.shape != old.shape:
                assert v.ndim == 5 and old.shape[1] == 8 and v.shape[1] == channels
                assert v.shape[0] == old.shape[0] and v.shape[2:] == old.shape[2:]
                x = torch.zeros_like(v)
                x[:, :min(channels, 8)] = old[:, :min(channels, 8)]
                src[k] = x
                changed.append(k)
        net.load_state_dict(src, strict=True)
        del ck, src
    return net.to(device), changed
