import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const compiled=ts.transpileModule(readFileSync(new URL('../lib/studio-request.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {requestApi,ApiRequestError}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));
const original=globalThis.fetch;
let calls=0;
try {
  globalThis.fetch=async()=>{calls++;if(calls===1)throw new TypeError('Failed to fetch');return Response.json({active_model:'M10'});};
  assert.deepEqual(await requestApi('/state',undefined,'/api/gpu'),{active_model:'M10'});assert.equal(calls,2);
  calls=0;globalThis.fetch=async()=>{calls++;return calls===1?new Response('unavailable',{status:502}):Response.json({sample_id:'test'});};
  assert.deepEqual(await requestApi('/samples/test/volume',undefined,'/api/gpu'),{sample_id:'test'});assert.equal(calls,2);
  calls=0;globalThis.fetch=async()=>{calls++;throw new TypeError('Failed to fetch');};
  await assert.rejects(requestApi('/train',{steps:100},'/api/gpu'),/may already be running/);assert.equal(calls,1);
  calls=0;globalThis.fetch=async()=>{calls++;return new Response('sign in',{status:401});};
  await assert.rejects(requestApi('/state',undefined,'/api/gpu'),/sign-in session/);assert.equal(calls,1);
  globalThis.fetch=async()=>Response.json({detail:"Upload not found"},{status:404});
  await assert.rejects(requestApi("/volumes/uploads/expired",undefined,"/api/gpu"),error=>error instanceof ApiRequestError&&error.status===404);
  const controller=new AbortController();controller.abort();calls=0;
  await assert.rejects(requestApi('/state',undefined,'/api/gpu',controller.signal),{name:'AbortError'});assert.equal(calls,0);
  console.log('PASS: transient GET recovery, 502 recovery, no duplicated POST, expired-session message, cancelled request');
} finally {globalThis.fetch=original;}
