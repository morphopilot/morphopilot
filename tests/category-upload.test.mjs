import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

// Exercise the real uploader after selecting a category other than the open workspace.
const source=readFileSync(new URL('../components/volume-library.tsx',import.meta.url),'utf8');
const ast=ts.createSourceFile('volume-library.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
let start;function visit(node){if(ts.isFunctionDeclaration(node)&&node.name?.text==='start')start=node.getText(ast);ts.forEachChild(node,visit);}visit(ast);
assert(start,'Locate upload implementation');
const code=ts.transpileModule(start,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText;
function harness(resumed){
  const calls=[],imports=[],storage=new Map(resumed?[['spot-file','spot-upload']]:[]);
  const c={file:{name:'same.tif',size:8},valid:true,control:{current:null},uploadRunning:{current:false},capabilities:{},endpoint:'/api/gpu/categories/spot',uploadCategory:'spot',initialCategory:'neurite',category:{label:'Spot'},tile:[25,192,192],spacing:[1,1,1],AbortController,DOMException,ApiRequestError:class extends Error{},uploadStorageKey:()=> 'spot-file',sizeLabel:String,
    setRunning:value=>c.running=value,setError:value=>c.error=value,setPhase:value=>c.phase=value,setSession:value=>c.session=value,
    localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    requestApi:async(path,body,endpoint)=>{calls.push({path,body,endpoint});if(path==='/volumes/uploads/spot-upload')return {upload:{id:'spot-upload',offset:8,size:8,volume_id:'spot-volume'}};if(path==='/volumes/uploads')return {upload:{id:'spot-upload',offset:0,size:8}};if(path==='/volumes/uploads/spot-upload/complete')return {volume:{id:'spot-volume',status:'importing'}};if(path==='/volumes/spot-volume')return {volume:{id:'spot-volume',status:'ready'}};throw new Error('Unexpected upload route '+path);},
    sendUploadChunks:async({endpoint})=>{calls.push({path:'chunks',endpoint});return {id:'spot-upload',offset:8,size:8};},
    onImported:(volume,category)=>imports.push({id:volume.id,status:volume.status,category}),
    setTimeout:fn=>setTimeout(fn,0),clearTimeout,
  };
  vm.createContext(c);vm.runInContext(code,c);return {c,calls,imports,storage};
}
test('new cross-category upload sends all bytes to chosen category and waits for ready tiles before redirect',async()=>{
  const {c,calls,imports,storage}=harness(false);await c.start();
  assert.equal(c.error,'');assert.equal(c.running,false);
  assert(calls.every(call=>call.endpoint==='/api/gpu/categories/spot'));
  assert.deepEqual(calls.map(call=>call.path),['/volumes/uploads','chunks','/volumes/uploads/spot-upload/complete','/volumes/spot-volume']);
  assert.deepEqual(imports,[{id:'spot-volume',status:'ready',category:'spot'}]);assert.equal(storage.size,0);
});
test('resumed completed upload stays in its selected category without sending its image again',async()=>{
  const {c,calls,imports}=harness(true);await c.start();
  assert.equal(c.error,'');assert.deepEqual(calls.map(call=>call.path),['/volumes/uploads/spot-upload','/volumes/spot-volume']);
  assert(calls.every(call=>call.endpoint==='/api/gpu/categories/spot'));
  assert.deepEqual(imports,[{id:'spot-volume',status:'ready',category:'spot'}]);
});

test('an upload completion after pause or unmount cannot navigate to another category',async()=>{
 const {c,imports,storage}=harness(true),request=c.requestApi;
 c.requestApi=async(...args)=>{const response=await request(...args);if(args[0]==='/volumes/spot-volume')c.control.current.abort();return response;};
 await c.start();assert.deepEqual(imports,[]);assert.equal(storage.get('spot-file'),'spot-upload');assert.match(c.error,/Upload paused/);
});
test('a second start click while upload is pending cannot create a duplicate import',async()=>{
 const {c,calls,imports}=harness(false),request=c.requestApi;let release;
 c.requestApi=async(...args)=>{if(args[0]==='/volumes/uploads')await new Promise(resolve=>release=resolve);return request(...args);};
 const first=c.start();await c.start();assert.equal(c.uploadRunning.current,true);release();await first;
 assert.equal(calls.filter(call=>call.path==='/volumes/uploads').length,1);assert.equal(imports.length,1);assert.equal(c.uploadRunning.current,false);
});
