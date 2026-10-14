import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const source=readFileSync(new URL('../components/volume-library.tsx',import.meta.url),'utf8');
const fn=source.match(/ async function selectVolume\([\s\S]+?(?=\n async function merge)/)[0];
const pending=[],chosen=[],errors=[];
const context={activeSelection:{current:null},onSelectVolume:()=>{},setDetail:()=>{},nextUnreviewed:tiles=>tiles[0],
 api:async path=>{const id=path.split('/').pop();return {volume:{id,status:'ready'},tiles:[{id:id+'_tile'}]};},
 onRefresh:()=>new Promise(resolve=>pending.push(resolve)),onSelectTile:async tile=>chosen.push(tile.id),setError:e=>errors.push(e)};
vm.createContext(context);vm.runInContext(ts.transpileModule(fn,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,context);
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const a=context.selectVolume('A');await settle();
const b=context.selectVolume('B');await settle();
pending[1]();await b;pending[0]();await a;
assert.deepEqual(chosen,['B_tile']);assert.deepEqual(errors,[]);
console.log('PASS: slow collection A refresh cannot replace newer collection B selection');
