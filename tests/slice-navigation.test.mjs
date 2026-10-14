import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const source=readFileSync(new URL('../lib/slice-navigation.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {createSliceWheel}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));
let view={z:11,depth:23,ready:true,drawing:false},changes=[];
const wheel=createSliceWheel(()=>view,z=>{view={...view,z};changes.push(z)});
function scroll(deltaX,deltaY,timeStamp,extra={}){
 let prevented=false,stopped=false;
 wheel({deltaX,deltaY,timeStamp,deltaMode:0,ctrlKey:false,preventDefault(){prevented=true},stopPropagation(){stopped=true},...extra});
 assert.ok(prevented,'Every gesture must prevent browser XY scrolling');
 assert.ok(stopped,'Every gesture must stay inside the volume viewer');
}
scroll(0,20,0);assert.equal(view.z,11);
scroll(0,20,20);assert.equal(view.z,12);
scroll(0,80,30);assert.equal(view.z,12,'Trackpad burst is throttled');
scroll(90,2,120);assert.equal(view.z,13,'Horizontal gesture navigates Z too');
scroll(-90,2,220);assert.equal(view.z,12);
scroll(0,-3,420,{deltaMode:1});assert.equal(view.z,11,'Mouse line units');
scroll(0,1,620,{deltaMode:2});assert.equal(view.z,12,'Mouse page units');
const count=changes.length;
view.drawing=true;scroll(0,80,820);assert.equal(changes.length,count,'A stroke stays on one Z slice');
view.drawing=false;view.ready=false;scroll(0,80,1020);assert.equal(changes.length,count);
view.ready=true;scroll(0,80,1220,{ctrlKey:true});assert.equal(changes.length,count,'Pinch must not zoom/pan the viewer');
view.z=22;scroll(0,80,1420);assert.equal(view.z,22);
view.z=0;scroll(0,-80,1620);assert.equal(view.z,0);
scroll(0,80,1820);assert.equal(view.z,1);
console.log('PASS: vertical/horizontal trackpad changes only Z; all gestures consumed; burst, bounds, loading, drawing and pinch guards');
