"""Native nnInteractive prompt network plus an initially inactive image-only stem."""
import os, sys, copy, hashlib
from pathlib import Path
import numpy as np
import torch
from torch import nn
from model_build import build_model, OFFICIAL
from training_utils import crop_with_padding, apply_augmentation, segmentation_loss
from prompt_simulation import create_prompt_sample
from nnInteractive.inference.inference_session import nnInteractiveInferenceSession
PATCH=(32,192,192)

class DualStem(nn.Module):
    def __init__(self,prompt=None):
        super().__init__()
        self.prompt=prompt if prompt is not None else build_model(8,False,device='cpu')[0]
        one=build_model(1,False,device='cpu')[0]
        self.auto_stem=copy.deepcopy(one.encoder.stem)
        del one
        self.sync_auto()
        self.auto_stem.requires_grad_(False)
        self.auto_ready=False
    def sync_auto(self):
        source=self.prompt.encoder.stem.state_dict();dest=self.auto_stem.state_dict()
        for k,v in dest.items():
            old=source[k]
            if old.shape!=v.shape:
                assert old.ndim==5 and old.shape[1]==8 and v.shape[1]==1,(k,old.shape,v.shape)
                old=old[:,:1].contiguous()
            dest[k]=old.detach().clone()
        self.auto_stem.load_state_dict(dest,strict=True)
        self.auto_ready=True
    def forward_auto(self,image):
        x=self.auto_stem(image);skips=[]
        for stage in self.prompt.encoder.stages:
            x=stage(x);skips.append(x)
        return self.prompt.decoder(skips)
    def forward(self,image,prompts):
        return self.prompt(torch.cat([image,prompts],dim=1))
    def payload(self,meta):
        return {'prompt_weights':{k:v.detach().cpu() for k,v in self.prompt.state_dict().items()},
                'auto_stem_weights':{k:v.detach().cpu() for k,v in self.auto_stem.state_dict().items()},
                'auto_ready':self.auto_ready,'meta':meta}

def load_dual(path=None,device='cuda'):
    if path:
        z=torch.load(path,map_location='cpu',weights_only=False)
        model=DualStem();model.prompt.load_state_dict(z['prompt_weights'],strict=True)
        model.auto_stem.load_state_dict(z['auto_stem_weights'],strict=True);model.auto_ready=z['auto_ready']
    else:
        model=DualStem(build_model(8,True,device='cpu')[0])
    return model.to(device)

def tensor_hash(module):
    h=hashlib.sha256()
    for k,v in module.state_dict().items():h.update(k.encode());h.update(v.detach().cpu().contiguous().numpy().tobytes())
    return h.hexdigest()

def normalize(raw):
    # Matches native session's nonzero-bbox normalization (sample standard deviation).
    raw=np.asarray(raw,dtype=np.float32)
    coords=np.nonzero(raw)
    if not len(coords[0]):raise ValueError('The image contains only zeros.')
    sl=tuple(slice(int(c.min()),int(c.max())+1) for c in coords)
    region=raw[sl]
    if region.size<2:raise ValueError('The volume needs at least two nonzero voxels for normalization.')
    mean=float(region.mean(dtype=np.float64));std=max(float(region.std(dtype=np.float64,ddof=1)),1e-8)
    return (raw-mean)/std

@torch.inference_mode()
def auto_predict(model,raw):
    image=normalize(raw);shape=image.shape
    starts=[sorted(set([*range(0,max(1,n-p+1),max(1,p//2)),max(0,n-p)])) for n,p in zip(shape,PATCH)]
    prob=np.zeros(shape,np.float32);count=np.zeros(shape,np.float32)
    import itertools
    for start in itertools.product(*starts):
        sl=tuple(slice(a,min(n,a+p)) for a,p,n in zip(start,PATCH,shape));part=image[sl]
        patch=np.zeros(PATCH,np.float32)
        target=tuple(slice((p-n)//2,(p-n)//2+n) for n,p in zip(part.shape,PATCH));patch[target]=part
        with torch.autocast('cuda',dtype=torch.bfloat16):
            pred=model.forward_auto(torch.from_numpy(patch[None,None]).cuda()).softmax(1)[0,1].float().cpu().numpy()
        prob[sl]+=pred[target];count[sl]+=1
    return (prob/np.maximum(count,1)>.5).astype(np.uint8)
