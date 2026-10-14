// Execute the actual production callbacks: backend /volume payloads omit
// volume_id, so collection identity must come from the latest /state metadata.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const filename=new URL('../app/studio.tsx',import.meta.url);
const source=readFileSync(filename,'utf8');
const ast=ts.createSourceFile(filename.pathname,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const callbacks=new Map();
function visit(node){
  if(ts.isVariableDeclaration(node)&&ts.isIdentifier(node.name)&&node.initializer&&ts.isCallExpression(node.initializer)&&node.initializer.expression.getText(ast)==='useCallback')callbacks.set(node.name.text,node.initializer.arguments[0].getText(ast));
  ts.forEachChild(node,visit);
}
visit(ast);
for(const name of ['refresh','loadVolume'])assert(callbacks.has(name),`Locate actual ${name} callback`);
const compiled=ts.transpileModule(['refresh','loadVolume'].map(name=>`var ${name}=${callbacks.get(name)};`).join('\n'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText;
const viewCode=ts.transpileModule(readFileSync(new URL('../lib/volume-view.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const {renderSlice}=await import('data:text/javascript;base64,'+Buffer.from(viewCode).toString('base64'));
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
const snapshot=(parent='A')=>({active_model:'corrected-M10',samples:[{id:'A_tile',volume_id:parent},{id:'B_tile',volume_id:'B'}],jobs:[],models:[]});
const payload=(id,pixel=20)=>({sample_id:id,shape:[23,4,4],image:Buffer.alloc(23*4*4,pixel).toString('base64'),mask:Buffer.alloc(23*4*4,1).toString('base64'),revision:3,confirmed:true,events:3,prompts:[]});
function harness(){
  const requests=[],writes=[],storage=new Map();
  const context={mounted:{current:true},stateSequence:{current:0},stateRef:{current:null},volumeSequence:{current:0},loadingVolume:{current:false},imageRef:{current:null},maskRef:{current:null},endpoint:'/api/gpu',URL,
    decode:value=>Uint8Array.from(Buffer.from(value,'base64')),renderSlice,
    window:{location:{href:'http://localhost/?dataset=neurite&volume=old&tile=old_tile&z=99'},history:{replaceState:(_state,_title,url)=>{context.window.location.href=url;writes.push(['url',url]);}}},
    localStorage:{setItem:(key,value)=>{storage.set(key,value);}},
    api:async(path,body)=>{requests.push({path,body});if(path==='/state')return snapshot();if(path.startsWith('/samples/')&&path.endsWith('/volume'))return payload(path.split('/')[2]);throw new Error(`Unexpected API request ${path}`);},
    setState:value=>{context.state=value;},setConnected:value=>{context.connected=value;},setConnectionError:value=>{context.connectionError=value;},
    setSample:value=>{context.sample=value;writes.push(['sample',value]);},setVolume:value=>{context.volume=value;writes.push(['volume',value.sample_id]);},setSelectedVolumeId:value=>{context.selectedVolumeId=value;writes.push(['parent',value]);},
    setLoading:value=>{context.loading=value;},setZ:value=>{context.z=typeof value==='function'?value(context.z):value;},setReviewed:()=>{},setEmptyReviewed:()=>{},setCursorPosition:()=>{},setError:value=>{context.error=value;},z:0,
  };
  vm.createContext(context);vm.runInContext(compiled,context);
  return {context,requests,writes,storage};
}

test('loading saved tile retains its parent when backend volume payload omits volume_id',async()=>{
  const {context:c,requests,storage}=harness();
  await c.refresh();
  assert.equal(await c.loadVolume('A_tile',11),true);
  assert.equal(payload('A_tile').volume_id,undefined,'Exercise the real API response shape');
  assert.equal(c.volume.volume_id,'A','Attach derived parent to the stored volume for cross-tab deletion detection');
  assert.equal(c.selectedVolumeId,'A');assert.equal(c.sample,'A_tile');assert.equal(c.z,11);
  assert.equal(c.maskRef.current[0],1,'Saved mask survives loading');
  const url=new URL(c.window.location.href);
  assert.equal(url.searchParams.get('volume'),'A');assert.equal(url.searchParams.get('tile'),'A_tile');assert.equal(url.searchParams.get('z'),'12');
  assert.equal(url.searchParams.get('dataset'),'neurite');
  assert.equal(storage.get('morphopilot-last-sample:/api/gpu'),'A_tile');
  assert.equal(c.state.active_model,'corrected-M10');
  assert.deepEqual(requests.map(request=>request.path),['/state','/samples/A_tile/volume']);
  assert(requests.every(request=>request.body===undefined),'Loading is read-only and never activates or trains a model');
});

test('superseded delayed A cannot replace newer B image, mask, parent or URL',async()=>{
  const {context:c,requests,writes}=harness();await c.refresh();
  const a=deferred(),b=deferred();
  c.api=(path,body)=>{requests.push({path,body});if(path==='/samples/A_tile/volume')return a.promise;if(path==='/samples/B_tile/volume')return b.promise;throw new Error(`Unexpected API request ${path}`);};
  const first=c.loadVolume('A_tile',11),second=c.loadVolume('B_tile',3);
  b.resolve(payload('B_tile',90));assert.equal(await second,true);
  const committedURL=c.window.location.href,committedWrites=writes.length;
  a.resolve(payload('A_tile',10));assert.equal(await first,false);
  assert.equal(c.sample,'B_tile');assert.equal(c.volume.sample_id,'B_tile');assert.equal(c.selectedVolumeId,'B');assert.equal(c.imageRef.current[0],90);assert.equal(c.maskRef.current[0],1);
  assert.equal(c.window.location.href,committedURL);assert.equal(writes.length,committedWrites);assert.equal(c.z,3);
  assert.equal(new URL(committedURL).searchParams.get('volume'),'B');assert.equal(new URL(committedURL).searchParams.get('z'),'4');
  assert.equal(c.loading,false);assert.equal(c.loadingVolume.current,false);assert.equal(c.state.active_model,'corrected-M10');
  assert.deepEqual(requests.map(request=>request.path),['/state','/samples/A_tile/volume','/samples/B_tile/volume']);
  assert(requests.every(request=>request.body===undefined));
});

test('stale state metadata cannot replace the parent source used by the next load',async()=>{
  const {context:c}=harness(),old=deferred(),fresh=deferred();let count=0;
  c.api=path=>{assert.equal(path,'/state');return ++count===1?old.promise:fresh.promise;};
  const first=c.refresh(),second=c.refresh();fresh.resolve(snapshot('A'));await second;old.resolve(snapshot('wrong-old-parent'));await first;
  c.api=async path=>{assert.equal(path,'/samples/A_tile/volume');return payload('A_tile');};
  assert.equal(await c.loadVolume('A_tile'),true);assert.equal(c.selectedVolumeId,'A');
  assert.equal(new URL(c.window.location.href).searchParams.get('volume'),'A');
  assert.equal(c.state.active_model,'corrected-M10');
});
