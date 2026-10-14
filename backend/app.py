"""MorphoPilot API. Storage stays on the attached volume; GPU work is serialized."""
import os,sys,json,time,uuid,threading,hashlib,secrets,base64,io,shutil,subprocess,traceback,zipfile
import importlib.util
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import tifffile
from PIL import Image,ImageDraw
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import JSONResponse,Response,FileResponse,StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel,Field,model_validator
from typing import Literal, Optional
from volume_store import VolumeStore, CHUNK_BYTES
from job_store import update as update_job_file, request_cancel
from categories import category_labels, category_id, category_list, category_origins, CurrentCategoryAlias
import category_registry
from demos import DemoStore
ROOT=Path(os.environ.get('MORPHOPILOT_DATA_DIR',str(Path(__file__).parent/'runtime')))
ROOT.mkdir(parents=True,exist_ok=True)
DATASET=os.environ.get('MORPHOPILOT_DATASET','uploads')
CATEGORY=category_id(DATASET,os.environ.get('MORPHOPILOT_CATEGORY'))
CATEGORY_LABEL=category_labels()[CATEGORY]
RELEASE_GPU_ON_IDLE=os.environ.get('MORPHOPILOT_RELEASE_GPU_ON_IDLE','true').lower() in {'1','true','yes'}
TOKEN=os.environ.get('MORPHOPILOT_API_TOKEN','')
if len(TOKEN)<24:raise RuntimeError('Set a strong MORPHOPILOT_API_TOKEN before starting the service')
LOCK=threading.RLock();POOL=ThreadPoolExecutor(max_workers=1);ENGINE=None
GPU_LOCK_PATH=Path(os.environ.get('MORPHOPILOT_GPU_LOCK_PATH',str(ROOT.parent/'morphopilot-gpu.lock')))
GPU_LOCK_PATH.parent.mkdir(parents=True,exist_ok=True)
for d in ['samples','models','jobs','snapshots']:(ROOT/d).mkdir(exist_ok=True)
# A runtime root belongs to exactly one category. Existing roots are adopted
# in place, without rewriting masks or model histories.
category_file=ROOT/'category.json'
try:
    with category_file.open('x') as stream:json.dump({'id':CATEGORY,'label':CATEGORY_LABEL},stream)
except FileExistsError:
    if json.loads(category_file.read_text()).get('id')!=CATEGORY:
        raise RuntimeError('This data directory belongs to a different category. Configure a separate MORPHOPILOT_DATA_DIR.')

def read(p,default=None):return json.loads(Path(p).read_text()) if Path(p).exists() else default

def atomic(value,p):
    if isinstance(value,dict) and (Path(p).name in {'meta.json','state.json'} or Path(p).parent==ROOT/'jobs'):
        value={**value,'category_id':CATEGORY}
    p=Path(p);tmp=p.with_suffix(f'.{uuid.uuid4().hex}.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));os.replace(tmp,p)
    if p.name=='meta.json' and p.parent.parent==ROOT/'samples' and 'VOLUMES' in globals():VOLUMES.sync_sample(value)

def save_array(a,p):
    p=Path(p);tmp=p.with_suffix('.tmp')
    with tmp.open('wb') as f:np.save(f,a,allow_pickle=False)
    os.replace(tmp,p)

def sample_dir(sid):
    if not sid.replace('_','').replace('-','').isalnum():raise HTTPException(400,'Invalid sample ID')
    p=ROOT/'samples'/sid
    if not (p/'meta.json').exists():raise HTTPException(404,'Sample not found')
    meta=read(p/'meta.json')
    if meta.get('category_id',CATEGORY)!=CATEGORY:raise HTTPException(409,'This sample belongs to a different data category.')
    if meta.get('volume_id') and VOLUMES._get('volumes',meta['volume_id'])['status']!='ready':raise HTTPException(404,'Sample is not in an active volume.')
    return p

def model_info(mid):
    if mid=='official':return {'id':'official','label':'Original pretrained model','phase':'official','auto_ready':False,'confirmed_count':0,'checkpoint':None,'category_id':CATEGORY}
    if not mid.replace('_','').replace('-','').isalnum():raise HTTPException(400,'Invalid model ID')
    v=read(ROOT/'models'/mid/'meta.json')
    if not v:raise HTTPException(404,'Model not found')
    if v.get('category_id',CATEGORY)!=CATEGORY:raise HTTPException(409,'This model belongs to a different data category.')
    return {**v,'category_id':CATEGORY}

def display_record(record):
    if record.get('category_id',CATEGORY)!=CATEGORY:raise HTTPException(409,'A stored record belongs to a different data category.')
    record={**record,'category_id':CATEGORY}
    for key in ('title','label','message','error'):
        value=record.get(key)
        if isinstance(value,str):
            value=value.replace('测试 ROI','Test ROI').replace('Original pretrained model','Original pretrained model').replace('双模式','Dual mode')
            if any('\u3400' <= c <= '\u9fff' for c in value):
                if key=='message':value={'completed':'Task completed.','cancelled':'Task cancelled.','running':'Task is running.','queued':'Task is queued.','failed':'Task failed.'}.get(record.get('status'),'Saved task record.')
                elif key=='error':value='This earlier task failed. See the server log for details.'
            record[key]=value
    return record

def state_config():return read(ROOT/'state.json',{'active_model':'official'})

def all_samples():
    ready={v['id'] for v in VOLUMES._all('volumes') if v['status']=='ready'} if 'VOLUMES' in globals() else set()
    return [s for p in sorted((ROOT/'samples').glob('*/meta.json')) if (s:=read(p)) and s.get('category_id',CATEGORY)==CATEGORY and (not s.get('volume_id') or s['volume_id'] in ready)]

def all_jobs():return sorted([read(p) for p in (ROOT/'jobs').glob('*.json')],key=lambda x:x['created_at'],reverse=True)

def require_idle():
    if any(j['status'] in ['queued','running'] for j in all_jobs()):raise HTTPException(409,'The GPU is processing another task. Please wait.')

def check_revision(meta,rev):
    if rev!=meta['revision']:raise HTTPException(409,'This ROI has changed. Reload it before editing.')

def require_neural_runtime():
    # CPU review installations intentionally omit torch. Report this before
    # queuing a GPU task; manual annotation and saved results remain usable.
    try:available=importlib.util.find_spec('torch') is not None
    except (ModuleNotFoundError,ValueError):available=False
    if not available:
        raise HTTPException(503,'Neural segmentation and training are unavailable in this CPU-only installation. Install the GPU requirements and configure the pretrained model weights on a CUDA server. Uploading, manual mask painting, review, and merging remain available.')

def image_for(p):
    meta=read(p/'meta.json')
    if meta.get('volume_id'):return VOLUMES.image_for(meta)
    if 'image_path' in meta:
        with np.load(meta['image_path'],allow_pickle=False) as d:return d['image'].astype(np.float32)
    return np.load(p/'image.npy',allow_pickle=False)

def prepare():
    """Start with an empty workspace; uploads and models are always explicit."""
    if (ROOT/'initialized.json').exists():return
    if not (ROOT/'state.json').exists():atomic({'active_model':'official'},ROOT/'state.json')
    atomic({'created_at':time.time(),'dataset':DATASET,'source':'Local workspace; upload a volume to begin.'},ROOT/'initialized.json')
prepare()
VOLUMES=VolumeStore(ROOT,LOCK,category=CATEGORY)
# Incomplete work is explicitly failed after a service restart; never report false completion.
for j in all_jobs():
    if j['status'] in ['queued','running']:
        j.update(status='failed',error='The service restarted before this task finished. Existing models and confirmed annotations are preserved.');atomic(j,ROOT/'jobs'/f"{j['id']}.json")
app=FastAPI(title='MorphoPilot',docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(CurrentCategoryAlias,category=CATEGORY)
@app.middleware('http')
async def auth(request,call_next):
    if not secrets.compare_digest(request.headers.get('authorization',''),f'Bearer {TOKEN}'):
        return JSONResponse({'detail':'Unauthorized'},status_code=401)
    if request.headers.get('x-morphopilot-category',CATEGORY)!=CATEGORY:
        return JSONResponse({'detail':'The configured service belongs to a different data category.'},status_code=409)
    response=await call_next(request);response.headers['Cache-Control']='no-store';return response

class CategoryRequest(BaseModel):
    category:Optional[str]=None
    category_id:Optional[str]=None

    @model_validator(mode='after')
    def category_matches(self):
        if any(value is not None and value!=CATEGORY for value in (self.category,self.category_id)):
            raise ValueError('The selected data category does not match this endpoint. Switch categories before submitting.')
        return self

class CategoryCreate(BaseModel):
    label:str=Field(min_length=1,max_length=256)

class Interaction(CategoryRequest):
    tool:Literal['point','bbox','scribble','lasso','brush','erase']
    include:bool=True
    points:list[list[int]]=Field(min_length=1,max_length=2002)
    radius:int=Field(default=3,ge=1,le=12)
    revision:int
class Revision(CategoryRequest):revision:int
class Confirm(Revision):
    reviewed:bool=False
    empty_background:bool=False
class TrainRequest(CategoryRequest):
    mode:Literal['prompt','auto']='prompt'
    steps:int=Field(default=100,ge=1,le=400)
    volume_id:Optional[str]=None
class UploadInit(CategoryRequest):
    name:str=Field(min_length=1,max_length=512)
    size:int=Field(ge=1)
    tile_shape:list[int]=Field(default_factory=lambda:[25,192,192],min_length=3,max_length=3)
    voxel_spacing:Optional[list[float]]=None
class VolumeTrashRequest(CategoryRequest):
    name:str=Field(min_length=1,max_length=180)
    revision:int=Field(ge=0)
class MergeRequest(CategoryRequest):draft:bool=False
class Activate(CategoryRequest):model_id:str
class DemoLoad(CategoryRequest):sample_id:Optional[str]=None

def raster(points,shape,radius=2,polygon=False):
    a=np.zeros(shape,np.uint8);z=points[0][0]
    im=Image.new('L',(shape[2],shape[1]));draw=ImageDraw.Draw(im);xy=[(p[2],p[1]) for p in points]
    if polygon and len(xy)>2:draw.polygon(xy,fill=1)
    else:
        draw.line(xy,fill=1,width=radius*2+1)
        for x,y in xy:draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill=1)
    a[z]=np.asarray(im);return a

def save_mask(p,mask,event,model_id=None):
    with LOCK:
        meta=read(p/'meta.json');history=read(p/'events.json',[])
        shutil.copy2(p/'mask.npy',p/'undo.npy');atomic(history,p/'undo_events.json');atomic({'last_model':meta.get('last_model')},p/'undo_meta.json')
        save_array(mask.astype(np.uint8),p/'mask.npy');history.append(event);atomic(history,p/'events.json')
        meta.update(revision=meta['revision']+1,events=len(history),confirmed=False,empty_background=False,last_model=model_id);atomic(meta,p/'meta.json')
        return meta

class InferenceEngine:
    def __init__(self):self.model_id=None;self.model=None;self.artifacts=None;self.session=None;self.session_key=None
    def clear(self):
        if self.session is not None:
            self.session.executor.shutdown(wait=True);self.session=None
        self.session_key=None
    def release(self):
        self.clear();self.artifacts=None;self.model=None;self.model_id=None
        import gc,torch
        gc.collect();torch.cuda.empty_cache()
    def load(self,mid):
        if self.model_id==mid:return
        self.release()
        import torch
        from model import nnInteractiveInferenceSession,OFFICIAL,DualStem
        native=nnInteractiveInferenceSession(device=torch.device('cuda'),use_torch_compile=False,torch_n_threads=4,interactions_storage='tensor',enable_undo=False)
        artifacts=native._load_model_artifacts_from_disk(str(OFFICIAL),0,'checkpoint_final.pth')
        self.model=DualStem(artifacts['network']).cuda()
        info=model_info(mid)
        if info['checkpoint']:
            payload=torch.load(info['checkpoint'],map_location='cpu',weights_only=False)
            self.model.prompt.load_state_dict(payload['prompt_weights'],strict=True)
            self.model.auto_stem.load_state_dict(payload['auto_stem_weights'],strict=True)
            self.model.auto_ready=payload['auto_ready']
        self.model.eval();artifacts['network']=self.model.prompt;self.artifacts=artifacts;self.model_id=mid
        native.executor.shutdown(wait=True)
    def get_session(self,p,mid):
        self.load(mid);meta=read(p/'meta.json');key=(meta['id'],mid,meta['revision'])
        if self.session_key==key:return self.session
        self.clear()
        import torch
        from model import nnInteractiveInferenceSession
        s=nnInteractiveInferenceSession(device=torch.device('cuda'),use_torch_compile=False,torch_n_threads=4,interactions_storage='tensor',enable_undo=False)
        s.initialize_from_loaded_artifacts(self.artifacts);s.set_image(image_for(p)[None])
        mask=np.load(p/'mask.npy',allow_pickle=False).copy();s.set_target_buffer(np.zeros_like(mask));s._finish_preprocessing_and_initialize_interactions()
        if mask.any():s.add_initial_seg_interaction(mask,run_prediction=False)
        self.session=s;self.session_key=key;return s
    def interact(self,p,req,mid):
        import torch
        s=self.get_session(p,mid);pts=req.points
        with torch.inference_mode():
            if req.tool=='point':s.add_point_interaction(tuple(pts[0]),req.include)
            elif req.tool=='bbox':
                a,b=pts[0],pts[-1];bbox=[[min(a[i],b[i]),max(a[i],b[i])+1] for i in range(3)]
                s.add_bbox_interaction(bbox,req.include)
            else:
                a=raster(pts,tuple(s.target_buffer.shape),req.radius,req.tool=='lasso')
                if req.tool=='lasso':s.add_lasso_interaction(a,req.include)
                else:s.add_scribble_interaction(a,req.include)
        meta=save_mask(p,np.asarray(s.target_buffer),{**req.model_dump(),'model_id':mid,'time':time.time()},mid)
        self.session_key=(meta['id'],mid,meta['revision'])
    def auto(self,p,mid):
        from model import auto_predict
        self.load(mid)
        if not self.model.auto_ready:raise ValueError('This model does not have an active Auto stem')
        mask=auto_predict(self.model,image_for(p));save_mask(p,mask,{'tool':'auto','model_id':mid,'time':time.time()},mid);self.clear()

def engine():
    global ENGINE
    if ENGINE is None:ENGINE=InferenceEngine()
    return ENGINE

def update_job(jid,**values):
    with LOCK:
        return update_job_file(ROOT/'jobs'/f'{jid}.json',**values)

def submit(kind,fn,**extra):
    require_idle();jid=uuid.uuid4().hex[:16];j={'id':jid,'kind':kind,'status':'queued','created_at':time.time(),'category_id':CATEGORY,**extra};atomic(j,ROOT/'jobs'/f'{jid}.json')
    def run():
        try:
            # All isolated workspaces share the same physical GPU. A process
            # lock serializes their jobs while storage and model state stay separate.
            import fcntl
            with GPU_LOCK_PATH.open('a+') as gpu_lock:
                fcntl.flock(gpu_lock,fcntl.LOCK_EX)
                if read(ROOT/'jobs'/f'{jid}.json').get('cancel_requested'):
                    update_job(jid,status='cancelled',message='Training cancelled before loading the model.');return
                try:
                    update_job(jid,status='running');result=fn(jid) or {}
                finally:
                    # The other category services share this physical GPU. Free
                    # the complete engine before releasing the process lock;
                    # the next interaction rebuilds from its saved current mask.
                    if RELEASE_GPU_ON_IDLE and ENGINE is not None:ENGINE.release()
            final=read(ROOT/'jobs'/f'{jid}.json')
            if final['status'] not in ['failed','cancelled']:update_job(jid,status='completed',**result)
        except Exception as e:
            traceback.print_exc();update_job(jid,status='failed',error=str(e))
    POOL.submit(run);return {'job':j}

def detected_gpu():
    try:
        info=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3,check=True).stdout.splitlines()[0].split(',')
        return f'{info[0].strip()} · {round(float(info[1])/1024)} GB'
    except Exception:
        # NVML/nvidia-smi can fail while CUDA inference still works. Do not
        # equate an unavailable monitoring query with an absent GPU.
        try:has_torch=importlib.util.find_spec('torch') is not None
        except (ModuleNotFoundError,ValueError):has_torch=False
        if has_torch:
            try:
                import torch
                if torch.cuda.is_available():
                    properties=torch.cuda.get_device_properties(0)
                    return f'{properties.name} · {round(properties.total_memory/1024**3)} GB'
            except Exception:pass
        return 'GPU status unavailable · check server diagnostics' if has_torch else 'CPU review mode · neural runtime not installed'
GPU_LABEL=detected_gpu()

@app.get('/health')
def health():return {'status':'ok','service':'morphopilot','category_id':CATEGORY}
@app.get('/categories')
def categories():return {'categories':category_list(CATEGORY),'current_category':CATEGORY,'can_create':category_registry.supervisor_available(),'max_custom_categories':category_registry.MAX_CUSTOM}
@app.post('/categories',status_code=202)
def create_category(req:CategoryCreate):
    try:record=category_registry.create(req.label)
    except category_registry.CategoryError as error:raise HTTPException(error.status_code,str(error))
    return {'category':{**category_registry.public_record(record),'available':False,'builtin':False}}
@app.post('/categories/{category_name}/retry',status_code=202)
def retry_category(category_name:str):
    try:record=category_registry.retry(category_name)
    except category_registry.CategoryError as error:raise HTTPException(error.status_code,str(error))
    return {'category':{**category_registry.public_record(record),'available':False,'builtin':False}}
@app.get('/state')
def state():
    with LOCK:
        samples=[display_record(s) for s in all_samples()];models=[model_info('official')]+sorted([read(p) for p in (ROOT/'models').glob('*/meta.json')],key=lambda x:x.get('created_at',0))
        return {**state_config(),'dataset':DATASET,'category_id':CATEGORY,'category':{'id':CATEGORY,'label':CATEGORY_LABEL},'samples':samples,'models':[{k:v for k,v in m.items() if k!='checkpoint'} for m in map(display_record,models)],'confirmed_count':sum(s['confirmed'] and s['split']=='annotation' for s in samples),'trainable_count':sum(s['confirmed'] and s['split']=='annotation' and s.get('training_eligible',True) for s in samples),'trainable_confirmed_count':sum(s['confirmed'] and s['split']=='annotation' and s.get('training_eligible',True) for s in samples),'jobs':[display_record(j) for j in all_jobs()[:10]],'gpu':GPU_LABEL,'volumes':VOLUMES.list(),'capabilities':{**VOLUMES.capabilities(),'categories':category_list(CATEGORY),'training_scope':'category','workspaces':[{'id':'neurite','label':'Neurite'}]+([{'id':'legacy_spot','label':'Spot'}] if category_origins()['spot'] else [])}}
DEMOS=DemoStore(ROOT,CATEGORY,sample_dir,model_info,image_for)

@app.get('/demos')
def demo_collections():
    with LOCK:return {'category_id':CATEGORY,'demos':DEMOS.list()}

@app.get('/demos/{demo_id}/slice')
def demo_slice(demo_id:str,sample_id:Optional[str]=None,z:Optional[int]=None,max_size:int=1024):
    with LOCK:return DEMOS.slice(demo_id,sample_id,z,max_size)

@app.post('/demos/{demo_id}/load')
def load_demo(demo_id:str,req:DemoLoad=DemoLoad()):
    with LOCK:
        require_idle();demo,sample=DEMOS.selection(demo_id,req.sample_id)
        mid=demo['model']['id']
        result={'category_id':CATEGORY,'demo_id':demo_id,'model_id':mid,'sample_id':sample['id'],'sample_ids':demo['sample_ids'],'z':sample['preferred_z']}
        if state_config()['active_model']!=mid:result.update(activate(Activate(model_id=mid)))
        return result

@app.get('/samples/{sid}/volume')
def volume(sid):
    with LOCK:
        p=sample_dir(sid);meta=read(p/'meta.json');raw=image_for(p);lo,hi=np.percentile(raw,[1,99.6]);image=np.clip((raw-lo)/max(hi-lo,1e-8)*255,0,255).astype(np.uint8);mask=np.load(p/'mask.npy',allow_pickle=False)
        return {'category_id':CATEGORY,'shape':list(raw.shape),'image':base64.b64encode(image.tobytes()).decode(),'mask':base64.b64encode(mask.tobytes()).decode(),'revision':meta['revision'],'confirmed':meta['confirmed'],'events':meta['events'],'model_id':meta.get('last_model'),'sample_id':sid,'can_undo':(p/'undo.npy').exists(),'prompts':[e for e in read(p/'events.json',[]) if e.get('tool') in ['point','bbox','scribble','lasso']]}
@app.post('/samples/{sid}/interaction')
def interaction(sid,req:Interaction):
    with LOCK:
        require_idle();p=sample_dir(sid);meta=read(p/'meta.json');check_revision(meta,req.revision)
        if meta['confirmed']:raise HTTPException(409,'Reopen this ROI before editing.')
        for pt in req.points:
            if len(pt)!=3 or any(v<0 or v>=n for v,n in zip(pt,meta['shape'])):raise HTTPException(422,'Coordinates are outside the volume.')
        if req.tool!='point' and len({p[0] for p in req.points})>1:raise HTTPException(422,'Draw each stroke on a single slice.')
        mid=state_config()['active_model']
        if req.tool in ['brush','erase']:
            mask=np.load(p/'mask.npy',allow_pickle=False);paint=raster(req.points,mask.shape,req.radius)>0;mask[paint]=int(req.tool=='brush' and req.include)
            save_mask(p,mask,{**req.model_dump(),'time':time.time()},meta.get('last_model'))
            if ENGINE:ENGINE.clear()
            return {'ok':True}
        if meta.get('inference_eligible') is False:raise HTTPException(422,'This tile has constant or insufficient image data for model normalization. Review it and confirm empty background, or use manual painting.')
        require_neural_runtime()
        return submit('interaction',lambda jid:engine().interact(p,req,mid),sample_id=sid)
@app.post('/samples/{sid}/undo')
def undo(sid,req:Revision):
    with LOCK:
        require_idle();p=sample_dir(sid);meta=read(p/'meta.json');check_revision(meta,req.revision)
        if meta['confirmed']:raise HTTPException(409,'Reopen this ROI before editing.')
        if not (p/'undo.npy').exists():raise HTTPException(400,'There is no edit to undo.')
        os.replace(p/'undo.npy',p/'mask.npy');events=read(p/'undo_events.json',[]);atomic(events,p/'events.json');meta.update(revision=meta['revision']+1,events=len(events),last_model=read(p/'undo_meta.json',{}).get('last_model'));atomic(meta,p/'meta.json')
        if ENGINE:ENGINE.clear()
        return {'ok':True}
@app.post('/samples/{sid}/confirm')
def confirm(sid,req:Confirm):
    with LOCK:
        require_idle();p=sample_dir(sid);meta=read(p/'meta.json');check_revision(meta,req.revision)
        if not req.reviewed:raise HTTPException(422,'Review the entire ROI before confirming.')
        if meta['split']!='annotation':raise HTTPException(409,'Test ROIs cannot be added to training.')
        if meta['confirmed']:return {'ok':True}
        mask=np.load(p/'mask.npy',allow_pickle=False)
        if not mask.any() and not req.empty_background:raise HTTPException(422,'The mask is empty. Mark it explicitly as reviewed empty background, or annotate foreground first.')
        if mask.any() and req.empty_background:raise HTTPException(422,'Only an empty mask can be confirmed as empty background.')
        snapshot=ROOT/'snapshots'/f'{sid}_r{meta["revision"]}.npz';np.savez_compressed(snapshot,image=image_for(p),mask=mask)
        meta.update(confirmed=True,empty_background=not bool(mask.any()),confirmed_at=time.time(),snapshot=str(snapshot),snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest());atomic(meta,p/'meta.json')
        return {'ok':True,'confirmed':True}
@app.post('/samples/{sid}/reset')
def reset(sid,req:Revision):
    with LOCK:
        require_idle();p=sample_dir(sid);meta=read(p/'meta.json');check_revision(meta,req.revision)
        if meta['confirmed']:raise HTTPException(409,'Reopen this ROI before editing.')
        save_mask(p,np.zeros(meta['shape'],np.uint8),{'tool':'reset','time':time.time()})
        atomic([],p/'events.json');meta=read(p/'meta.json');meta['events']=0;atomic(meta,p/'meta.json')
        if ENGINE:ENGINE.clear()
        return {'ok':True}
@app.post('/samples/{sid}/reopen')
def reopen(sid,req:Revision):
    with LOCK:
        require_idle();p=sample_dir(sid);meta=read(p/'meta.json');check_revision(meta,req.revision);meta.update(confirmed=False,empty_background=False,revision=meta['revision']+1);atomic(meta,p/'meta.json');return {'ok':True}
@app.post('/samples/{sid}/auto')
def auto(sid,req:Revision):
    with LOCK:
        require_idle();p=sample_dir(sid);meta=read(p/'meta.json');check_revision(meta,req.revision)
        if meta['confirmed']:raise HTTPException(409,'Use an unconfirmed ROI for testing, or reopen this ROI first.')
        mid=state_config()['active_model']
        if not model_info(mid)['auto_ready']:raise HTTPException(400,'Create an Auto stem first.')
        if meta.get('inference_eligible') is False:raise HTTPException(422,'This tile has constant or insufficient image data for model normalization. Review and confirm empty background, or paint manually.')
        require_neural_runtime()
        return submit('auto',lambda jid:engine().auto(p,mid),sample_id=sid)
@app.post('/models/activate')
def activate(req:Activate):
    with LOCK:
        require_idle();model_info(req.model_id);require_neural_runtime()
        def load(jid):
            engine().load(req.model_id);atomic({'active_model':req.model_id},ROOT/'state.json');return {'model_id':req.model_id}
        return submit('activate',load)

def save_native_export(model,out):
    import torch
    from model import OFFICIAL
    dest=out/'nnInteractive_model';dest.mkdir(exist_ok=True)
    for p in OFFICIAL.iterdir():
        if p.is_file() and (p.suffix in ['.json','.txt'] or p.name in ['LICENSE','LICENSE.txt']):shutil.copy2(p,dest/p.name)
    (dest/'fold_0').mkdir(exist_ok=True)
    ck=torch.load(OFFICIAL/'fold_0/checkpoint_final.pth',map_location='cpu',weights_only=False)
    metadata={k:v for k,v in ck.items() if k not in ['network_weights','optimizer_state','optimizer_state_dict','grad_scaler_state']}
    metadata['network_weights']={k:v.detach().cpu() for k,v in model.prompt.state_dict().items()}
    torch.save(metadata,dest/'fold_0/checkpoint_final.pth')

@app.post('/models/convert')
def convert(req:CategoryRequest=CategoryRequest()):
    with LOCK:
        require_idle();parent=state_config()['active_model'];info=model_info(parent)
        if parent=='official':raise HTTPException(400,'Complete at least one Prompt fine-tuning round before creating an Auto stem.')
        require_neural_runtime()
        def work(jid):
            import torch
            from model import load_dual,tensor_hash
            engine().release();m=load_dual(info['checkpoint']);before=tensor_hash(m.prompt);m.sync_auto();assert tensor_hash(m.prompt)==before
            mid='dual_'+jid;out=ROOT/'models'/mid;out.mkdir();torch.save(m.payload({'parent':parent,'conversion':'copy current prompt stem; select image input channel','prompt_unchanged':True}),out/'checkpoint.pth')
            meta={'id':mid,'label':f'Dual mode · {mid[-6:]}','phase':'dual','auto_ready':True,'confirmed_count':info['confirmed_count'],'parent':parent,'created_at':time.time(),'checkpoint':str(out/'checkpoint.pth')};save_native_export(m,out);atomic(meta,out/'meta.json')
            del m;torch.cuda.empty_cache();atomic({'active_model':mid},ROOT/'state.json');return {'model_id':mid,'message':'Dual-mode model created and activated. The Prompt weights are unchanged.'}
        return submit('convert',work)
@app.post('/train')
def train(req:TrainRequest):
    with LOCK:
        require_idle();parent=state_config()['active_model'];info=model_info(parent)
        if req.mode=='auto' and not info['auto_ready']:raise HTTPException(400,'Create a dual-mode model first.')
        if req.volume_id and VOLUMES.detail(req.volume_id)['volume']['status']!='ready':raise HTTPException(409,'This volume is not ready.')
        # By default every confirmed annotation in this category contributes,
        # including legacy ROIs and all uploaded volumes. Explicit volume_id
        # remains a backwards-compatible collection-only training request.
        scoped=[s for s in all_samples() if (req.volume_id is None or s.get('volume_id')==req.volume_id) and s['confirmed'] and s['split']=='annotation']
        skipped=[s['id'] for s in scoped if not s.get('training_eligible',True)]
        samples=[{'id':s['id'],'category_id':CATEGORY,'volume_id':s.get('volume_id'),'revision':s['revision'],'snapshot':s['snapshot'],'sha256':s['snapshot_sha256'],'empty_background':s.get('empty_background',False)} for s in scoped if s.get('training_eligible',True)]
        if not samples:raise HTTPException(400,'Confirm at least one trainable ROI in this category first. Constant or insufficient-data tiles are retained for merging but excluded from training.')
        require_neural_runtime()
        def work(jid):
            engine().release();mid=req.mode+'_'+jid;out=ROOT/'models'/mid;out.mkdir()
            if read(ROOT/'jobs'/f'{jid}.json').get('cancel_requested'):(out/'cancel').touch()
            cfg={'category_id':CATEGORY,'training_scope':'volume' if req.volume_id else 'category','parent':parent,'checkpoint':info['checkpoint'],'mode':req.mode,'steps':req.steps,'samples':samples,'out':str(out),'job_path':str(ROOT/'jobs'/f'{jid}.json'),'model_id':mid,'volume_id':req.volume_id,'skipped_normalization_tiles':skipped};atomic(cfg,out/'config.json')
            with (out/'train.log').open('w') as log:
                proc=subprocess.Popen([sys.executable,'-u',str(Path(__file__).parent/'train.py'),str(out/'config.json')],stdout=log,stderr=subprocess.STDOUT)
                code=proc.wait()
            job=read(ROOT/'jobs'/f'{jid}.json')
            if code or not job.get('training_finished'):
                if job['status'] not in ['cancelled','failed']:raise RuntimeError('Training did not finish. Check the service log.')
                return {}
            record=read(out/'training.json');meta={'id':mid,'label':f'{req.mode.capitalize()} · {len(samples)} ROI · {mid[-6:]}','phase':req.mode,'auto_ready':record['auto_ready'],'confirmed_count':len(samples),'parent':parent,'steps':req.steps,'created_at':time.time(),'checkpoint':str(out/'checkpoint.pth'),'category_id':CATEGORY,'training_scope':cfg['training_scope']}
            from model import load_dual
            m=load_dual(str(out/'checkpoint.pth'),device='cpu');save_native_export(m,out);del m;atomic(meta,out/'meta.json')
            atomic({'active_model':mid},ROOT/'state.json')
            return {'model_id':mid,'message':f'Fine-tuning complete. The new {CATEGORY_LABEL} model is active for this category.'}
        return submit('train',work,steps=req.steps,step=0,volume_id=req.volume_id,training_scope='volume' if req.volume_id else 'category',confirmed_count=len(samples),skipped_normalization_tiles=skipped)
@app.get('/jobs/{jid}')
def job(jid):
    if not jid.isalnum():raise HTTPException(400,'Invalid job ID')
    v=read(ROOT/'jobs'/f'{jid}.json')
    if not v:raise HTTPException(404,'Job not found')
    return display_record(v)
@app.post('/jobs/{jid}/cancel')
def cancel(jid):
    j=job(jid)
    if not request_cancel(ROOT/'jobs'/f'{jid}.json'):raise HTTPException(409,'This task has finished or is saving its model and can no longer be cancelled.')
    # Locate only this task's output directory.
    for p in (ROOT/'models').glob(f'*_{jid}'):(p/'cancel').touch()
    return {'ok':True}
@app.get('/samples/{sid}/export')
def export(sid):
    with LOCK:
        p=sample_dir(sid);mask=np.load(p/'mask.npy',allow_pickle=False);buf=io.BytesIO();tifffile.imwrite(buf,mask,metadata={'axes':'ZYX'},compression='zlib')
    return Response(buf.getvalue(),media_type='image/tiff',headers={'Content-Disposition':f'attachment; filename="{sid}_mask.tif"'})
@app.post('/samples/upload')
async def upload(request:Request,name:str='sample.tif',category:Optional[str]=None):
    if category is not None and category!=CATEGORY:raise HTTPException(422,'The upload category does not match this endpoint.')
    with LOCK:require_idle()
    raw=await request.body()
    if len(raw)>64*1024*1024:raise HTTPException(413,'Upload a 3D ROI smaller than 64 MB.')
    try:
        if name.lower().endswith('.npz'):
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                if sum(x.file_size for x in z.infolist())>128*1024*1024:raise ValueError('The uncompressed volume is too large.')
            with np.load(io.BytesIO(raw),allow_pickle=False) as d:a=d['image'].copy()
        elif name.lower().endswith(('.tif','.tiff')):
            with tifffile.TiffFile(io.BytesIO(raw)) as tf:
                series=tf.series[0]
                if series.size*series.dtype.itemsize>128*1024*1024:raise ValueError('The uncompressed volume is too large.')
                if len(series.axes)!=3 or series.axes[-2:]!='YX' or series.axes[0] not in 'ZQI':raise ValueError('Upload a single-channel ZYX TIFF stack. Color, channel and time axes are not supported.')
                a=series.asarray()
        else:raise ValueError('Use a TIFF stack or an NPZ containing an image array.')
        if a.ndim!=3 or any(n<2 for n in a.shape) or any(n>m for n,m in zip(a.shape,[96,512,512])):raise ValueError('Use a single-channel ZYX volume, up to 96 × 512 × 512. Crop larger images before uploading.')
        if np.count_nonzero(a)<2:raise ValueError('The volume needs at least two nonzero voxels for normalization.')
        if not np.issubdtype(a.dtype,np.number) or np.iscomplexobj(a) or not np.isfinite(a).all() or np.std(a)<1e-8:raise ValueError('The image must contain finite, real, non-constant values.')
    except Exception as e:raise HTTPException(422,str(e))
    with LOCK:
        require_idle();sid='upload_'+uuid.uuid4().hex[:12];p=ROOT/'samples'/sid;p.mkdir();save_array(a.astype(np.float32),p/'image.npy');save_array(np.zeros(a.shape,np.uint8),p/'mask.npy');atomic([],p/'events.json');atomic({'id':sid,'title':Path(name).name[:80],'shape':list(a.shape),'confirmed':False,'split':'annotation','revision':0,'events':0,'source_group':sid},p/'meta.json')
    return {'id':sid,'category_id':CATEGORY}


@app.post('/volumes/uploads')
def create_volume_upload(req:UploadInit):
    return {'upload':VOLUMES.create_upload(req.name,req.size,req.tile_shape,req.voxel_spacing)}
@app.get('/volumes/uploads/{uid}')
def volume_upload_status(uid:str):return {'upload':VOLUMES.upload_status(uid)}
@app.post('/volumes/uploads/{uid}/chunk')
async def upload_volume_chunk(uid:str,request:Request,offset:int):
    declared=request.headers.get('content-length')
    if declared and int(declared)>CHUNK_BYTES:raise HTTPException(413,'Upload chunks must not exceed 8 MiB.')
    data=bytearray()
    async for part in request.stream():
        if len(data)+len(part)>CHUNK_BYTES:raise HTTPException(413,'Upload chunks must not exceed 8 MiB.')
        data.extend(part)
    return {'upload':VOLUMES.append_chunk(uid,offset,data)}
@app.post('/volumes/uploads/{uid}/complete')
def complete_volume_upload(uid:str):return {'volume':VOLUMES.complete_upload(uid)}
@app.get('/volumes')
def collections():return {'volumes':VOLUMES.list()}
@app.get('/volumes/trash')
def trashed_collections():return {'category_id':CATEGORY,'items':VOLUMES.list_trash()}
@app.post('/volumes/trash/{tid}/restore')
def restore_collection(tid:str,req:CategoryRequest):
    with LOCK:
        require_idle();return VOLUMES.restore_volume(tid)
@app.post('/volumes/{vid}/trash')
def trash_collection(vid:str,req:VolumeTrashRequest):
    with LOCK:
        require_idle()
        result=VOLUMES.trash_volume(vid,req.name,req.revision)
        if ENGINE:ENGINE.clear()
        return result
@app.get('/volumes/{vid}')
def collection_detail(vid:str):return VOLUMES.detail(vid)
@app.get('/volumes/{vid}/slice')
def collection_slice(vid:str,z:int=0,max_size:int=1024):return VOLUMES.slice(vid,z,max_size)
@app.post('/volumes/{vid}/merge')
def merge_collection(vid:str,req:MergeRequest):return {'volume':VOLUMES.merge(vid,req.draft)}
@app.get('/volumes/{vid}/export')
def export_collection(vid:str,draft:bool=False):
    with LOCK:
        path=VOLUMES.export_path(vid,draft)
        stream=path.open('rb');size=os.fstat(stream.fileno()).st_size
    # Keep a descriptor to the exact exported revision. A concurrent move to
    # Trash or new merge cannot invalidate an already-started download.
    def chunks():
        try:
            while block:=stream.read(1024*1024):yield block
        finally:stream.close()
    return StreamingResponse(chunks(),media_type='image/tiff',background=BackgroundTask(stream.close),headers={
        'Content-Length':str(size),'Content-Disposition':f'attachment; filename="{vid}_{"DRAFT" if draft else "reviewed"}_mask.tif"'})

@app.get('/report/{relative_path:path}')
def experiment_report(relative_path:str):
    configured=os.environ.get('MORPHOPILOT_REPORT_DIR')
    if not configured:raise HTTPException(404,'No experiment report is configured for this workspace.')
    root=Path(configured).resolve();target=(root/relative_path).resolve()
    if not target.is_relative_to(root) or not target.is_file() or target.suffix.lower() not in {'.html','.png','.svg','.json','.csv','.tif','.tiff','.zip','.txt'}:
        raise HTTPException(404,'Report file not found.')
    return FileResponse(target)

async def proxy_category(category_name:str,relative_path:str,request:Request):
    if category_name not in category_labels():raise HTTPException(404,'Unknown data category.')
    origin=category_origins()[category_name]
    if not origin:raise HTTPException(503,f'The {category_labels()[category_name]} category is not configured.')
    if not relative_path or any(not p or p in {'..','.'} or '\\' in p for p in relative_path.split('/')) or relative_path.split('/')[0] in {'categories','workspaces'}:
        raise HTTPException(400,'Invalid category path.')
    import httpx
    body=await request.body()
    if len(body)>64*1024*1024:raise HTTPException(413,'Maximum file size: 64 MB')
    client=httpx.AsyncClient(timeout=90,follow_redirects=False)
    url=origin.rstrip('/')+'/'+relative_path
    if request.url.query:url+='?'+request.url.query
    try:
        upstream=await client.send(client.build_request(request.method,url,content=body,headers={'Authorization':f'Bearer {TOKEN}','X-MorphoPilot-Category':category_name,'Content-Type':request.headers.get('content-type','application/json'),'Accept-Encoding':'identity'}),stream=True)
        if 300<=upstream.status_code<400:
            await upstream.aclose();await client.aclose();raise HTTPException(502,'Unexpected category redirect')
    except httpx.HTTPError:
        await client.aclose();raise HTTPException(502,f'The {category_labels()[category_name]} category is temporarily unavailable.')
    async def chunks():
        try:
            async for chunk in upstream.aiter_raw():yield chunk
        finally:
            await upstream.aclose();await client.aclose()
    headers={k:v for k,v in upstream.headers.items() if k in {'content-type','content-disposition','content-encoding'}}
    return StreamingResponse(chunks(),status_code=upstream.status_code,headers=headers)

@app.api_route('/categories/{category_name}/{relative_path:path}',methods=['GET','POST'])
async def category_workspace(category_name:str,relative_path:str,request:Request):
    return await proxy_category(category_name,relative_path,request)

@app.api_route('/workspaces/legacy_spot/{relative_path:path}',methods=['GET','POST'])
async def legacy_spot_workspace(relative_path:str,request:Request):
    return await proxy_category('spot',relative_path,request)
