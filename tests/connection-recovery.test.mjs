import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const source=readFileSync(new URL('../app/studio.tsx',import.meta.url),'utf8');
const refresh=source.match(/const refresh=useCallback\(async\(\)=>\{([\s\S]*?)\n \},\[api,workspace\]\);/)[1];
const poll=source.match(/useEffect\(\(\)=>\{const timer=setInterval\(\(\)=>\{([\s\S]*?)\},6000\);/)[1];
const pending=[];let connected=false,connectionError='',actionError='An annotation request failed.';
const context={mounted:{current:true},stateSequence:{current:0},stateRef:{current:null},imageRef:{current:true},loadingVolume:{current:false},busyRef:{current:false},sample:'roi',initialVolumeId:undefined,initialCollection:undefined,
  api:()=>new Promise((resolve,reject)=>pending.push({resolve,reject})),
  setState:()=>{},setConnected:v=>{connected=v;},setConnectionError:v=>{connectionError=v;},setError:v=>{actionError=v;}};
vm.createContext(context);
vm.runInContext(ts.transpileModule(`globalThis.refresh=async()=>{${refresh}};globalThis.poll=()=>{${poll}};`,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,context);
const settle=()=>new Promise(resolve=>setImmediate(resolve));
// A slow older poll must not restore an obsolete error after newer recovery.
context.poll();const fresh=context.refresh(),freshState={samples:[],active_model:'current-model'};pending[1].resolve(freshState);await fresh;
pending[0].reject(new Error('Old poll failed'));await settle();
assert.equal(connected,true);assert.equal(connectionError,'');assert.equal(context.stateRef.current,freshState);
// Current failure is visible; the next successful poll clears only that failure.
context.poll();pending[2].reject(new Error('Temporary outage'));await settle();
assert.equal(connected,false);assert.equal(connectionError,'Temporary outage');
context.poll();pending[3].resolve({samples:[]});await settle();
assert.equal(connected,true);assert.equal(connectionError,'');assert.equal(actionError,'An annotation request failed.');
console.log('PASS: actual poll callbacks clear recovered errors, ignore stale failures, preserve operation errors');
