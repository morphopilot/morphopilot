"""One explicitly requested incremental training round; immutable input snapshots."""
import os,json,time,sys,random,hashlib
from collections import OrderedDict
from pathlib import Path
from job_store import update as update_job_file, cancel_requested, begin_finalization
import numpy as np
import torch
from model import load_dual,tensor_hash,normalize,PATCH,crop_with_padding,apply_augmentation,segmentation_loss,create_prompt_sample

def atomic(value,path):
    p=Path(path);q=p.with_suffix('.tmp');q.write_text(json.dumps(value,ensure_ascii=False,indent=2));q.replace(p)

def run(config_path):
    cfg=json.loads(Path(config_path).read_text());out=Path(cfg['out']);job_path=Path(cfg['job_path'])
    mode=cfg['mode'];seed=260100+int(time.time())%100000
    if cancel_requested(job_path):
        update_job_file(job_path,status='cancelled',message='Training cancelled before loading the model.');return
    torch.manual_seed(seed);np.random.seed(seed);random.seed(seed);torch.set_num_threads(4)
    torch.backends.cudnn.benchmark=True
    model=load_dual(cfg['checkpoint']);original_auto=tensor_hash(model.auto_stem)
    if mode=='prompt':
        model.prompt.requires_grad_(True);model.auto_stem.requires_grad_(False);model.auto_ready=False
        params=list(model.prompt.parameters())
    else:
        if not model.auto_ready:raise ValueError('Create an Auto stem from a Prompt model first.')
        model.prompt.requires_grad_(True);model.prompt.encoder.stem.requires_grad_(False);model.auto_stem.requires_grad_(True)
        params=[p for p in model.parameters() if p.requires_grad]
    model.train();optimizer=torch.optim.AdamW(params,lr=1e-5,weight_decay=1e-4,fused=True)
    # Large tiled uploads can contain thousands of confirmed ROIs. Keep at most
    # two decoded samples in RAM; selection remains uniform over all snapshots.
    samples=cfg['samples'];cache=OrderedDict()
    if not samples:raise ValueError('No confirmed training snapshots were supplied.')
    def get_sample(index):
        if index in cache:
            cache.move_to_end(index);return cache[index]
        item=samples[index];snapshot=Path(item['snapshot'])
        expected=item.get('snapshot_sha256') or item.get('sha256')
        if expected:
            digest=hashlib.sha256()
            with snapshot.open('rb') as stream:
                for chunk in iter(lambda:stream.read(8*1024*1024),b''):digest.update(chunk)
            if digest.hexdigest()!=expected:raise ValueError(f'Training snapshot checksum mismatch: {snapshot.name}')
        with np.load(snapshot,allow_pickle=False) as d:
            raw=d['image'].copy();label=d['mask'].astype(np.int64)
        cache[index]=(normalize(raw),label,np.argwhere(label>0))
        if len(cache)>2:cache.popitem(last=False)
        return cache[index]
    rng=np.random.default_rng(seed);losses=[];t0=time.monotonic();steps=cfg['steps']
    for step in range(1,steps+1):
        if (out/'cancel').exists() or cancel_requested(job_path):
            update_job_file(job_path,status='cancelled',message='Training cancelled. No new model was published.');return
        image,label,fg=get_sample(int(rng.integers(len(samples))))
        center=tuple(int(v) for v in (fg[int(rng.integers(len(fg)))] if len(fg) and rng.random()<.8 else [rng.integers(n) for n in image.shape]))
        if image.shape[0] <= PATCH[0]:center=(image.shape[0]//2,center[1],center[2])
        x=crop_with_padding(image,center,PATCH,0);y=crop_with_padding(label,center,PATCH,-1)
        x,y,valid=apply_augmentation(x,y,y>=0,rng);y=np.where(valid,y,-1)
        im=torch.from_numpy(x[None,None]).cuda();target=torch.from_numpy(y[None].astype(np.int64)).cuda()
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast('cuda',dtype=torch.bfloat16):
            if mode=='prompt':
                scenario=str(rng.choice(['point','bbox2d','scribble','prev_seg','mixed']))
                prompt=create_prompt_sample((y==1)&valid,valid,rng,scenario).interactions
                logits=model(im,torch.from_numpy(prompt[None]).cuda())
            else:logits=model.forward_auto(im)
            loss=segmentation_loss(logits,target)[0]
        if not torch.isfinite(loss):raise RuntimeError('Training produced a non-finite loss.')
        loss.backward();torch.nn.utils.clip_grad_norm_(params,1);optimizer.step();losses.append(float(loss.detach()))
        if step==1 or step%5==0 or step==steps:
            update_job_file(job_path,status='running',step=step,steps=steps,loss=float(np.mean(losses[-5:])),message=f'{len(samples)} confirmed ROIs · {mode} fine-tuning',elapsed_seconds=time.monotonic()-t0)
    if (out/'cancel').exists():update_job_file(job_path,cancel_requested=True)
    if not begin_finalization(job_path):return
    frozen_auto_unchanged=tensor_hash(model.auto_stem)==original_auto
    if mode=='prompt' and not frozen_auto_unchanged:raise AssertionError('Inactive auto stem was modified')
    meta={'mode':mode,'category_id':cfg.get('category_id'),'training_scope':cfg.get('training_scope','volume'),'steps':steps,'seed':seed,'parent':cfg['parent'],'confirmed_count':len(samples),'samples':cfg['samples'],'losses':losses,'training_seconds':time.monotonic()-t0,'inactive_auto_unchanged':frozen_auto_unchanged,'auto_ready':model.auto_ready,'normalization':'native nonzero bounding box; sample std','patch_zyx':PATCH,'prompt_supervision':'simulated prompts derived only from user-confirmed masks','selection':'final round checkpoint; no validation-based best claim'}
    torch.save(model.payload(meta),out/'checkpoint.tmp');os.replace(out/'checkpoint.tmp',out/'checkpoint.pth');atomic(meta,out/'training.json')
    # The parent registers and exports the model before declaring completion.
    update_job_file(job_path,status='running',training_finished=True,message='Optimization complete. Exporting the model…')
if __name__=='__main__':
    try:run(sys.argv[1])
    except Exception as e:
        import traceback
        traceback.print_exc();cfg=json.loads(Path(sys.argv[1]).read_text());p=Path(cfg['job_path']);update_job_file(p,status='failed',error=str(e));sys.exit(1)
