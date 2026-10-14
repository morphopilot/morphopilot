import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';
const compile=source=>ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const helpers=await import('data:text/javascript;base64,'+Buffer.from(compile(readFileSync(new URL('../lib/demo-library.ts',import.meta.url),'utf8'))).toString('base64'));

test('demo link pins the exact category, collection, saved sample and Z',()=>{
  const url=new URL(helpers.demoAnnotationHref('spot','spot-rounds','spot_crop_1',19),'http://localhost');
  assert.equal(url.searchParams.get('category'),'spot');assert.equal(url.searchParams.get('collection'),'experiments');assert.equal(url.searchParams.get('sample'),'spot_crop_1');assert.equal(url.searchParams.get('z'),'20');
  assert.equal(url.searchParams.has('volume'),false);assert.equal(url.searchParams.has('tile'),false);
});
test('cross-category or uploaded samples cannot be silently substituted for a demo ROI',()=>{
  const samples=[{id:'spot1'},{id:'upload1',volume_id:'vol1'}];
  assert.equal(helpers.resolveDemoSample(samples,'spot1').id,'spot1');
  assert.throws(()=>helpers.resolveDemoSample(samples,'neurite1'),/unavailable in the selected category/);
  assert.throws(()=>helpers.resolveDemoSample(samples,'upload1'),/unavailable in the selected category/);
  assert.match(helpers.demoMaskDescription({round_history:{round:10},recorded_mask:true}),/created with M9/);
  assert.doesNotMatch(helpers.demoMaskDescription({round_history:{round:10},recorded_mask:false,mask_model_label:"Later edited model"}),/created with M9/);
});
function findNode(file,predicate){const source=readFileSync(new URL(file,import.meta.url),'utf8'),ast=ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let found;function visit(node){if(predicate(node,ast))found=node;ts.forEachChild(node,visit);}visit(ast);assert(found);return {found,ast};}
const {found:initial,ast:studio}=findNode('../app/studio.tsx',node=>ts.isVariableDeclaration(node)&&node.name.getText()==='loadInitialSelection');
const initialJS=compile(`var run=${initial.initializer.arguments[0].getText(studio)};`);
function selectionContext(id='spot1') {
  const selected=[],notices=[];
  const context={initialSampleId:id,initialZ:'20',initialVolumeId:'previous-upload',resolveDemoSample:helpers.resolveDemoSample,firstRoundSlice:()=>0,
    loadVolume:async(id,z)=>{selected.push({id,z});return true;},setNotice:value=>notices.push(value),localStorage:{getItem(){throw new Error('Explicit selection must not read the previous upload');}}};
  vm.createContext(context);vm.runInContext(initialJS,context);return {context,selected,notices};
}
test('actual initial load callback selects the requested demo even when the last selection was an uploaded volume',async()=>{
  const {context,selected}=selectionContext();await context.run({samples:[{id:'spot1'},{id:'previous-tile',volume_id:'previous-upload'}]});
  assert.equal(JSON.stringify(selected),JSON.stringify([{id:'spot1',z:19}]));
});
test('actual initial load callback fails clearly instead of falling back when a demo sample is missing',async()=>{
  const {context,selected}=selectionContext('missing');await assert.rejects(context.run({samples:[{id:'spot1'}]}),/unavailable/);assert.equal(selected.length,0);
});
const {found:load,ast:demoAST}=findNode('../components/demo-library.tsx',node=>ts.isFunctionDeclaration(node)&&node.name?.text==='loadDemoSelection');
const loadJS=compile(load.getText(demoAST).replace(/^export /,''));
const demo={id:'spot-demo',model:{id:'spot-m10'},samples:[{id:'spot1',shape:[25,192,192]}]};
function loadContext(responses) {
  const requests=[],context={URLSearchParams,setTimeout:fn=>setTimeout(fn,0),clearTimeout,demoAnnotationHref:helpers.demoAnnotationHref,
    requestApi:async(path,body,endpoint,signal)=>{requests.push({path,body,endpoint});signal.throwIfAborted();const value=responses.shift();if(value instanceof Error)throw value;return value;}};
  vm.createContext(context);vm.runInContext(loadJS,context);return {context,requests};
}
const response=(job)=>({category_id:'spot',demo_id:'spot-demo',model_id:'spot-m10',sample_id:'spot1',z:19,...(job?{job}:{})});
test('actual Load demo activates its bound model once and waits for the asynchronous GPU job before navigation',async()=>{
  const {context,requests}=loadContext([response({id:'job1',status:'queued'}),{id:'job1',status:'running'},{id:'job1',status:'completed'}]);
  const href=await context.loadDemoSelection('/api/gpu/categories/spot','spot',demo,'spot1',new AbortController().signal);
  assert.equal(new URL(href,'http://localhost').searchParams.get('sample'),'spot1');
  assert.deepEqual(requests.map(request=>request.path),['/demos/spot-demo/load','/jobs/job1','/jobs/job1']);
  assert.equal(requests.filter(request=>request.body).length,1);assert(requests.every(request=>request.endpoint==='/api/gpu/categories/spot'));
});
test('failed activation or mismatched category cannot navigate to annotation',async()=>{
  let {context}=loadContext([response({id:'job1',status:'failed',error:'GPU busy'})]);
  await assert.rejects(context.loadDemoSelection('/api/gpu/categories/spot','spot',demo,'spot1',new AbortController().signal),/GPU busy/);
  ({context}=loadContext([{...response(),category_id:'neurite'}]));
  await assert.rejects(context.loadDemoSelection('/api/gpu/categories/spot','spot',demo,'spot1',new AbortController().signal),/different demo/);
});

const {found:libraryRefresh,ast:libraryAST}=findNode('../components/data-library-page.tsx',node=>ts.isVariableDeclaration(node)&&node.name.getText()==='refresh');
const refreshJS=compile(`var refresh=${libraryRefresh.initializer.arguments[0].getText(libraryAST)};`);
function refreshContext(demoResponse) {
  const output={},context={sequence:{current:0},lifecycle:{current:{signal:new AbortController().signal}},workspace:'spot',endpoint:'/api/gpu/categories/spot',
    requestApi:async path=>path==='/volumes'?{volumes:[{id:'vol_saved'}]}:path==='/state'?{category_id:'spot',active_model:'spot-m10'}:path==='/volumes/trash'?{items:[]}:demoResponse instanceof Error?Promise.reject(demoResponse):demoResponse};
  for(const key of ['Refreshing','Volumes','ModelState','Demos','Error','DemoIndexError','Loading','Trash','TrashError'])context[`set${key}`]=value=>{output[key]=value;};
  vm.createContext(context);vm.runInContext(refreshJS,context);return {context,output};
}
test('broken or missing demo configuration does not hide normal customer uploads',async()=>{
  const {context,output}=refreshContext(new Error('Demo model file is missing'));
  await context.refresh();assert.equal(output.Volumes[0].id,'vol_saved');assert.equal(output.ModelState.active_model,'spot-m10');
  assert.equal(output.Error,'');assert.match(output.DemoIndexError,/model file is missing/);assert.equal(output.Loading,false);assert.equal(output.Demos.length,0);
});
test('wrong-category demo payload is rejected while selected category uploads remain available',async()=>{
  const {context,output}=refreshContext({category_id:'neurite',demos:[{id:'wrong'}]});
  await context.refresh();assert.equal(output.Volumes[0].id,'vol_saved');assert.equal(output.Demos.length,0);assert.match(output.DemoIndexError,/another category/);
});
test('a user leaving during model activation cannot receive a late annotation redirect',async()=>{
  const controller=new AbortController(),{context,requests}=loadContext([response({id:'job1',status:'queued'})]);
  const pending=context.loadDemoSelection('/api/gpu/categories/spot','spot',demo,'spot1',controller.signal);controller.abort();
  await assert.rejects(pending,error=>error.name==='AbortError');assert.equal(requests.length,1);
});

test('ROI labels show the round once and identify the actual saved crop',()=>{
  assert.equal(helpers.demoSampleLabel({id:'neurite_crop_5',title:'Round 05 · ROI 05',round:5}),'Round 05 · neurite_crop_5');
  assert.equal(helpers.demoSampleLabel({id:'spot8',title:'ROI 08',round:8}),'Round 08 · spot8');
});

class ApiRequestError extends Error {constructor(message,status){super(message);this.status=status;}}
function removedUploadContext(status) {
 const {context,selected,notices}=selectionContext(undefined);
 Object.assign(context,{initialSampleId:undefined,initialZ:undefined,endpoint:'/api/gpu/categories/neurite',localStorage:{getItem:()=>null},volumeSequence:{current:1},mounted:{current:true},ApiRequestError,encodeURIComponent,api:async()=>{throw new ApiRequestError('unavailable',status);}});
 return {context,selected,notices};
}
test('a deleted uploaded-image bookmark opens a preserved confirmed demo with an explicit notice',async()=>{
 const {context,selected,notices}=removedUploadContext(404);
 await context.run({samples:[{id:'demo-round-1',confirmed:true,round_history:{round:1}}]});
 assert.equal(selected[0].id,'demo-round-1');assert.match(notices[0],/previous uploaded image was removed/);
});
test('authentication and server failures never silently substitute a demo for an uploaded image',async()=>{
 for(const status of [401,503]){const {context,selected}=removedUploadContext(status);await assert.rejects(context.run({samples:[{id:'demo',confirmed:true,round_history:{round:1}}]}));assert.equal(selected.length,0);}
 const {context,selected}=removedUploadContext(404);await assert.rejects(context.run({samples:[{id:'new-upload',volume_id:'vol1',confirmed:true}]}));assert.equal(selected.length,0);
});


test('Load this ROI preserves the inspected preview slice through model activation',async()=>{
  const {context}=loadContext([response({id:'job1',status:'queued'}),{id:'job1',status:'completed'}]);
  const href=await context.loadDemoSelection('/api/gpu/categories/spot','spot',demo,'spot1',new AbortController().signal,1);
  const link=new URL(href,'http://localhost');assert.equal(link.searchParams.get('z'),'2');assert.equal(link.searchParams.get('sample'),'spot1');
});
test('Load demo card retains the server preferred slice when there is no preview selection',async()=>{
  const {context}=loadContext([response()]);
  const href=await context.loadDemoSelection('/api/gpu/categories/spot','spot',demo,'spot1',new AbortController().signal);
  assert.equal(new URL(href,'http://localhost').searchParams.get('z'),'20');
});
test('invalid preview slices do not activate a model or navigate',async()=>{
  for(const z of [-1,25,1.5,NaN]){const {context,requests}=loadContext([response()]);await assert.rejects(context.loadDemoSelection('/api/gpu/categories/spot','spot',demo,'spot1',new AbortController().signal,z),/preview slice is unavailable/);assert.equal(requests.length,0);}
});
