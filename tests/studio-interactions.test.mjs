// Run from the repository root: node --test tests/studio-interactions.test.mjs
// Exact production functions are extracted with TypeScript AST. No browser,
// server import, real HTTP requests, or production storage writes occur.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import vm from 'node:vm';
import test from 'node:test';

const root=process.env.MORPHOPILOT_TEST_ROOT||process.cwd();
const require=createRequire(path.join(root,'package.json')),ts=require('typescript');
const filename=path.join(root,'app/studio.tsx'),source=readFileSync(filename,'utf8');
const ast=ts.createSourceFile(filename,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const functions=new Map(),callbacks=new Map(),bindings=new Map(),effects=[];
function visit(n){
 if(ts.isFunctionDeclaration(n)&&n.name)functions.set(n.name.text,n.getText(ast));
 if(ts.isVariableDeclaration(n)&&ts.isIdentifier(n.name)&&n.initializer&&ts.isCallExpression(n.initializer)&&n.initializer.expression.getText(ast)==='useCallback')callbacks.set(n.name.text,n.initializer.arguments[0].getText(ast));
 if(ts.isVariableDeclaration(n)&&ts.isIdentifier(n.name)&&n.initializer)bindings.set(n.name.text,n.initializer.getText(ast));
 if(ts.isCallExpression(n)&&n.expression.getText(ast)==='useEffect')effects.push(n.arguments[0].getText(ast));
 ts.forEachChild(n,visit);
}
visit(ast);
const boot=effects.find(e=>e.includes('loadInitialSelection(next)')&&e.includes('refresh().then'));
const poll=effects.find(e=>e.includes('setInterval(')&&e.includes('refresh().then'));
const lifecycle=effects.find(e=>e.includes('lifecycle.current=new AbortController'));
assert(boot&&poll&&lifecycle,'Locate the actual startup, polling and lifecycle effects');
const compile=s=>ts.transpileModule(s,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText;
const compiled=compile(['refresh','loadVolume','loadInitialSelection'].map(n=>{assert(callbacks.has(n),n);return `var ${n}=${callbacks.get(n)};`;}).join('\n')+'\n'+['perform','pointer','down','move','cancelPointer','up','chooseSample','upload'].map(n=>{assert(functions.has(n),n);return functions.get(n)}).join('\n')+`\nvar bootEffect=${boot};var pollEffect=${poll};var lifecycleEffect=${lifecycle};`+'\n'+['current','selectionReady','annotationAllowed'].map(n=>{assert(bindings.has(n),n);return `Object.defineProperty(globalThis,${JSON.stringify(n)},{get:()=>(${bindings.get(n)})});`;}).join('\n'));
const volumeCode=ts.transpileModule(readFileSync(path.join(root,'lib/volume-view.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const {renderSlice}=await import('data:text/javascript;base64,'+Buffer.from(volumeCode).toString('base64'));
async function importHelper(relative){const code=ts.transpileModule(readFileSync(path.join(root,relative),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;return import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));}
const {firstRoundSlice}=await importHelper('lib/round-history.ts');
const {resolveLibrarySelection}=await importHelper('lib/library-selection.ts');
const {resolveDemoSample}=await importHelper('lib/demo-library.ts');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject}};
const tick=async()=>{for(let i=0;i<8;i++)await Promise.resolve()};
const payload=(id,value=20)=>({sample_id:id,shape:[3,8,8],image:Buffer.alloc(192,value).toString('base64'),mask:Buffer.alloc(192).toString('base64'),revision:0,confirmed:false,events:0,can_undo:false,prompts:[]});
const state=(model='official')=>({active_model:model,samples:[{id:'A',split:'annotation'},{id:'B',split:'annotation'}],models:[],jobs:[],confirmed_count:0});
function harness(){
 const captures=new Set(),actions=[],writes=[],storage=new Map(),initialState=state();
 const c={mounted:{current:true},lifecycle:{current:new AbortController()},volumeSequence:{current:0},stateSequence:{current:0},stateRef:{current:initialState},busyRef:{current:false},loadingVolume:{current:false},pointerOwner:{current:null},imageRef:{current:null},maskRef:{current:null},stroke:{current:[]},sample:'old',volume:null,state:initialState,shape:[3,8,8],z:1,tool:'brush',include:true,radius:3,connected:true,workspace:'neurite',category:{promptHint:'Place a point on a neurite.'},endpoint:'/api/gpu/categories/neurite',initialVolumeId:undefined,initialSampleId:undefined,initialCollection:undefined,initialTileId:undefined,initialZ:undefined,selectedVolumeId:null,AbortController,Uint8Array,Number,Math,URL,decode:s=>Uint8Array.from(Buffer.from(s,'base64')),renderSlice,firstRoundSlice,resolveLibrarySelection,resolveDemoSample,encodeURIComponent,
   window:{location:{href:'http://localhost/?dataset=neurite'},history:{replaceState:(_state,_title,url)=>{c.window.location.href=url;}}},localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
   api:async p=>{throw Error('Unexpected API request '+p)},fetch:async()=>{throw Error('Unexpected upload')},setInterval:fn=>{c.poll=fn;return 1},clearInterval:()=>{},
   action:(p,b)=>{actions.push({path:p,body:JSON.parse(JSON.stringify(b))})},
   setSample:v=>{c.sample=v;writes.push(['sample',v])},setVolume:v=>{c.volume=v;c.shape=v.shape;writes.push(['volume',v.sample_id])},setSelectedVolumeId:v=>c.selectedVolumeId=v,setState:v=>c.state=v,setZ:v=>c.z=typeof v==='function'?v(c.z):v,setBusy:v=>c.working=v,setLoading:v=>c.loading=v,setConnected:v=>c.connected=v,setConnectionError:v=>c.connectionError=v,setReviewed:v=>c.reviewed=v,setEmptyReviewed:v=>c.emptyReviewed=v,setCursorPosition:v=>c.cursorPosition=v,setError:v=>c.error=v,setNotice:v=>c.notice=v,setDrawing:v=>c.drawing=v
 };
 const context=vm.createContext(c);vm.runInContext(compiled,context);
 const canvas={getBoundingClientRect:()=>({top:0,left:0,width:8,height:8}),setPointerCapture:id=>captures.add(id),hasPointerCapture:id=>captures.has(id),releasePointerCapture:id=>captures.delete(id)};
 const event=(id,x=1,y=1,primary=true)=>({pointerId:id,isPrimary:primary,button:0,clientX:x,clientY:y,currentTarget:canvas,preventDefault(){}});
 const seed=(id='old',value=20)=>{if(!c.state.samples.some(sample=>sample.id===id))c.state.samples.push({id,split:'annotation'});const v=payload(id,value);c.sample=id;c.volume=v;c.shape=v.shape;c.imageRef.current=c.decode(v.image);c.maskRef.current=c.decode(v.mask)};
 return{c,actions,writes,event,seed};
}

test('latest volume selection commits ID, volume and pixels together',async()=>{
 const {c}=harness(),a=deferred(),b=deferred();c.api=p=>p.includes('/A/')?a.promise:b.promise;
 const first=c.loadVolume('A'),second=c.loadVolume('B');b.resolve(payload('B',90));await second;
 assert.equal(c.sample,'B');assert.equal(c.volume.sample_id,'B');assert.equal(c.imageRef.current[0],90);assert.equal(c.loading,false);
 a.resolve(payload('A',10));await first;
 assert.equal(c.sample,'B');assert.equal(c.volume.sample_id,'B');assert.equal(c.imageRef.current[0],90);assert.equal(c.loadingVolume.current,false);
});

test('latest failed load preserves the existing matching image and ID',async()=>{
 const {c,seed}=harness();seed('old',30);c.api=async()=>{throw Error('download failed')};
 await assert.rejects(c.loadVolume('new'),/download failed/);assert.equal(c.sample,'old');assert.equal(c.volume.sample_id,'old');assert.equal(c.imageRef.current[0],30);assert.equal(c.loading,false);
});

test('mismatched server sample and incomplete payload never commit',async()=>{
 const {c,seed}=harness();seed('old',30);c.api=async()=>payload('wrong');
 await assert.rejects(c.loadVolume('new'),/different ROI/);assert.equal(c.sample,'old');assert.equal(c.volume.sample_id,'old');
 c.api=async()=>({...payload('new'),image:''});await assert.rejects(c.loadVolume('new'),/Incomplete/);assert.equal(c.sample,'old');assert.equal(c.imageRef.current[0],30);
});

test('upload success followed by download failure keeps the prior selection',async()=>{
 const {c,seed}=harness();seed('old',30);c.fetch=async()=>({ok:true,json:async()=>({id:'uploaded'})});c.api=async p=>{if(p==='/state')return state();throw Error('upload volume failed')};
 await c.upload({name:'sample.tif'});assert.equal(c.error,'upload volume failed');assert.equal(c.sample,'old');assert.equal(c.volume.sample_id,'old');assert.equal(c.imageRef.current[0],30);assert.equal(c.busyRef.current,false);
});

test('old state responses cannot revert the active model or connection',async()=>{
 const {c}=harness(),a=deferred(),b=deferred();let n=0;c.api=()=>++n===1?a.promise:b.promise;
 const first=c.refresh(),second=c.refresh();b.resolve(state('new-model'));await second;a.resolve(state('old-model'));await first;
 assert.equal(c.state.active_model,'new-model');assert.equal(c.connected,true);
});

test('late boot state response cannot replace a user selection',async()=>{
 const {c}=harness(),start=deferred();const calls=[];
 c.api=p=>{calls.push(p);if(p==='/state')return start.promise;return Promise.resolve(payload(p.includes('/B/')?'B':'A'))};
 const cleanup=c.bootEffect();await c.loadVolume('B');start.resolve(state());await tick();
 assert.equal(c.sample,'B');assert.equal(c.volume.sample_id,'B');assert(!calls.includes('/samples/A/volume'));cleanup();
});

test('pending initial polling cannot reload a default ROI after data arrives',async()=>{
 const {c}=harness(),pending=deferred();const calls=[];
 c.api=p=>{calls.push(p);if(p==='/state')return pending.promise;return Promise.resolve(payload('B'))};
 const cleanup=c.pollEffect();c.poll();await c.loadVolume('B');pending.resolve(state());await tick();
 assert.equal(c.sample,'B');assert.equal(c.volume.sample_id,'B');assert.equal(calls.filter(p=>p.includes('/volume')).length,1);cleanup();
});

test('secondary pointer move, up and cancel do not affect the owner stroke',()=>{
 const {c,event,seed,actions}=harness();seed();c.down(event(1));c.move(event(2,6,6,false));c.up(event(2,6,6,false));c.cancelPointer(event(2,6,6,false));
 assert.equal(c.pointerOwner.current.id,1);assert.equal(c.stroke.current.length,1);assert.equal(actions.length,0);
 c.move(event(1,2,2));c.up(event(1,3,3));assert.equal(actions.length,1);assert.equal(actions[0].body.points.length,3);assert(actions[0].body.points.every(p=>p[1]!==6&&p[2]!==6));assert.equal(c.pointerOwner.current,null);
 c.up(event(1,3,3));assert.equal(actions.length,1,'A second pointerup must not double-submit');
});

test('stroke submission uses the starting sample, revision, Z, tool and polarity',()=>{
 const {c,event,seed,actions}=harness();seed();c.volume.revision=7;c.radius=5;c.down(event(1));
 c.sample='other';c.z=2;c.tool='erase';c.include=false;c.radius=12;c.up(event(1,3,3));
 assert.equal(actions.length,1);assert.equal(actions[0].path,'/samples/old/interaction');assert.equal(actions[0].body.revision,7);assert.equal(actions[0].body.tool,'brush');assert.equal(actions[0].body.include,true);assert.equal(actions[0].body.radius,5);assert(actions[0].body.points.every(p=>p[0]===1));
});

test('owner cancellation discards drawing without changing the mask',()=>{
 const {c,event,seed,actions}=harness();seed();c.down(event(1));c.move(event(1,4,4));c.cancelPointer(event(1));c.up(event(1,4,4));
 assert.equal(actions.length,0);assert.equal(c.pointerOwner.current,null);assert.equal(c.stroke.current.length,0);assert.equal(c.drawing.length,0);
});

test('perform locks synchronously and releases after failure',async()=>{
 const {c}=harness(),gate=deferred();let called=0;const first=c.perform(async()=>{called++;await gate.promise;throw Error('failure')});
 await c.perform(async()=>{called++});assert.equal(called,1);assert.equal(c.busyRef.current,true);
 gate.resolve();await first;assert.equal(c.error,'failure');assert.equal(c.busyRef.current,false);assert.equal(c.working,false);
 await c.perform(async()=>{called++});assert.equal(called,2);
});

test('perform cannot change sample or model during a stroke',async()=>{
 const {c,event,seed}=harness();seed();c.down(event(1));let called=0;await c.perform(async()=>called++);assert.equal(called,0);assert.equal(c.busyRef.current,false);
});

test('annotation is blocked while loading, offline, confirmed, busy or mismatched',()=>{
 for(const configure of [c=>c.loadingVolume.current=true,c=>c.connected=false,c=>c.volume.confirmed=true,c=>c.busyRef.current=true,c=>c.sample='other',c=>c.selectedVolumeId='another-parent',c=>c.state.jobs=[{status:'running'}],c=>c.state.samples=[]]){
  const {c,event,seed}=harness();seed();configure(c);c.down(event(1));assert.equal(c.pointerOwner.current,null);assert.equal(c.stroke.current.length,0);
 }
});

test('unmount aborts lifecycle and prevents pending volume/state commits',async()=>{
 const {c,seed}=harness();seed();const a=deferred(),b=deferred();c.api=p=>p==='/state'?b.promise:a.promise;
 const cleanup=c.lifecycleEffect(),signal=c.lifecycle.current.signal,volume=c.loadVolume('new'),snapshot=c.refresh();cleanup();
 a.resolve(payload('new',90));b.resolve(state('new'));await Promise.all([volume,snapshot]);assert.equal(signal.aborted,true);assert.equal(c.sample,'old');assert.equal(c.volume.sample_id,'old');assert.equal(c.state.active_model,'official');
});

// Category identity is part of the data/model boundary, even if the server misroutes a response.
test('a state response from another category cannot replace the model or samples',async()=>{
 const {c}=harness();const original=c.state;c.api=async()=>({...state('spot-model'),category_id:'spot'});
 await assert.rejects(c.refresh(),/different data category/);
 assert.equal(c.state,original);assert.equal(c.stateRef.current,original);assert.equal(c.connected,false);
});

test('explicit experiment collection ignores the remembered uploaded volume',async()=>{
 const {c}=harness();c.initialCollection='experiments';c.localStorage.setItem(`morphopilot-last-sample:${c.endpoint}`,'B');
 const next={...state(),samples:[{id:'A',split:'annotation'},{id:'B',split:'annotation',volume_id:'uploaded-volume'}]};
 c.stateRef.current=next;c.api=async path=>{assert.equal(path,'/samples/A/volume');return payload('A');};
 await c.loadInitialSelection(next);assert.equal(c.sample,'A');assert.equal(c.selectedVolumeId,null);
 assert.equal(new URL(c.window.location.href).searchParams.get('collection'),'experiments');
});
