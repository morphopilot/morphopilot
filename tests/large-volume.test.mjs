import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const compiled=ts.transpileModule(readFileSync(new URL('../lib/large-volume.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {sendUploadChunks,nextUnreviewed,previewTileAt,tileStatus,tileReviewAppearance,uploadStorageKey}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));
const tiles=[{id:'a',tile_index:0,confirmed:true},{id:'b',tile_index:1,confirmed:false},{id:'c',tile_index:2,confirmed:false}];
assert.equal(nextUnreviewed(tiles,'a').id,'b');assert.equal(nextUnreviewed(tiles,'c').id,'b');assert.equal(nextUnreviewed(tiles.map(t=>({...t,confirmed:true}))),undefined);
assert.equal(tileStatus({confirmed:true,empty_background:true}),'Confirmed empty');assert.equal(tileStatus({confirmed:false,revision:3}),'In progress');
// Saved but unconfirmed annotations must remain unobscured, including after an undo/reset.
assert.equal(tileReviewAppearance({confirmed:false,events:11,revision:11}).hatch,false);
assert.equal(tileReviewAppearance({confirmed:false,events:0,revision:12}).label,'In progress · not confirmed');
assert.equal(tileReviewAppearance({confirmed:false,events:0,revision:0}).hatch,true);
assert.equal(tileReviewAppearance({confirmed:true,events:0,revision:1,empty_background:true}).label,'Confirmed empty');
assert.equal(tileReviewAppearance({confirmed:true,events:11,revision:11}).hatch,false);
// Absent legacy metadata is unknown, never evidence that a tile has not been started.
assert.equal(tileReviewAppearance({confirmed:false}).hatch,false);
assert.equal(tileReviewAppearance({confirmed:false}).label,'Not confirmed');
// Non-divisible shapes must use the server's integer sampling stride, not a resize ratio.
const slice={shape:[513,513],source_shape:[25,1025,1025],stride:2,z:0,tiles:[{id:'first',bounds_zyx:[[0,25],[0,192],[0,192]],confirmed:true},{id:'next',bounds_zyx:[[0,25],[0,192],[192,384]],confirmed:false}]};
assert.equal(previewTileAt(slice,10,95.99).id,'first');assert.equal(previewTileAt(slice,10,96).id,'next');
const file=new File(['abcdefghij'],'large.tif',{lastModified:123});
assert.notEqual(uploadStorageKey('/api',file,[25,192,192],[1,1,1]),uploadStorageKey('/api',file,[25,96,96],[1,1,1]));
let session={id:'upload1',offset:0,size:10,status:'uploading',chunk_size:4};let requests=[],progress=[],statusReads=0;
globalThis.fetch=async(url,options)=>{const offset=Number(new URL(url,'http://localhost').searchParams.get('offset'));requests.push({offset,text:await options.body.text()});session={...session,offset:Math.min(offset+4,10)};if(offset===0)throw new TypeError('Response lost after server write');return Response.json({upload:session});};
const result=await sendUploadChunks({endpoint:'/api',file,session:{...session},signal:new AbortController().signal,onProgress:s=>progress.push(s.offset),readStatus:async()=>{statusReads++;return session;}});
assert.equal(result.offset,10);assert.equal(statusReads,1);assert.deepEqual(requests,[{offset:0,text:'abcd'},{offset:4,text:'efgh'},{offset:8,text:'ij'}]);assert.deepEqual(progress,[4,8,10]);
// A resumed upload starts at its acknowledged offset and never rereads the full file.
requests=[];session={...session,offset:8};await sendUploadChunks({endpoint:'/api',file,session,signal:new AbortController().signal,onProgress:()=>{},readStatus:async()=>session});assert.deepEqual(requests,[{offset:8,text:'ij'}]);
let count=0;globalThis.fetch=async()=>{count++;return Response.json({detail:'Invalid image size'},{status:400});};
await assert.rejects(sendUploadChunks({endpoint:'/api',file,session:{...session,offset:0},signal:new AbortController().signal,onProgress:()=>{},readStatus:async()=>{throw new Error('Must not retry rejected mutations');}}),/Invalid image size/);assert.equal(count,1);
globalThis.fetch=async()=>Response.json({detail:'Conflicting chunk. Resume at offset 4.'},{status:409});
await assert.rejects(sendUploadChunks({endpoint:'/api',file,session:{...session,offset:0},signal:new AbortController().signal,onProgress:()=>{},readStatus:async()=>({...session,offset:4})}),/Conflicting chunk/);
console.log('PASS: exact stride mapping, next tile wraparound, explicit empty status, resumable chunk upload, lost-response recovery and rejected-chunk handling');
