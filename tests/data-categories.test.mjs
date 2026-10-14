import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

async function load(path){
  const code=ts.transpileModule(readFileSync(new URL(path,import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
  return import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
}
const {DATA_CATEGORIES,resolveDataCategory,categoryEndpoint,categoryAvailable,categoryLibraryHref}=await load('../lib/data-categories.ts');
const {uploadStorageKey}=await load('../lib/large-volume.ts');
test('dataset links resolve to their category; explicit categories take precedence',()=>{
  assert.equal(resolveDataCategory(undefined,'neurite'),'neurite');
  assert.equal(resolveDataCategory(undefined,'spot'),'spot');
  assert.equal(resolveDataCategory('cell','spot'),'cell');
  assert.equal(resolveDataCategory('organ'),'organ');
  assert.equal(resolveDataCategory('unknown'),'neurite');
  assert.equal(resolveDataCategory(undefined,'spot'),'spot');
  assert.deepEqual(DATA_CATEGORIES.map(category=>category.id),['neurite','spot','cell','organ']);
});
test('each category has separate API routes, navigation and resumable upload state',()=>{
  const file={name:'same-file.tif',size:12345,lastModified:7};
  const endpoints=DATA_CATEGORIES.map(category=>categoryEndpoint(category.id));
  assert.equal(new Set(endpoints).size,4);
  assert.equal(new Set(endpoints.map(endpoint=>uploadStorageKey(endpoint,file,[25,192,192],[1,1,1]))).size,4);
  assert.equal(categoryEndpoint('spot'),'/api/gpu/categories/spot');
  assert.equal(categoryLibraryHref('cell'),'/library?category=cell');
  assert.equal(categoryAvailable('organ'),true);
  assert.equal(categoryAvailable('organ',[{id:'organ',available:false}]),false);
  assert.equal(categoryAvailable('cell',[{id:'cell',available:true}]),true);
});
test('actual fine-tune action includes all confirmed category data rather than the selected volume',async()=>{
  const source=readFileSync(new URL('../app/studio.tsx',import.meta.url),'utf8');
  const ast=ts.createSourceFile('studio.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  let fn;function visit(node){if(ts.isFunctionDeclaration(node)&&node.name?.text==='train')fn=node.getText(ast);ts.forEachChild(node,visit);}visit(ast);
  assert(fn,'Locate actual training callback');
  const requests=[],notices=[];
  const context={category:{label:'Spot'},selectedVolumeId:'vol_visible_only',steps:'25',perform:async fn=>fn(),setNotice:message=>notices.push(message),api:async(path,body)=>{requests.push({path,body});return {job:{id:'job_spot'}};},waitJob:async()=>({model_id:'spot_next'}),refresh:async()=>{}};
  vm.createContext(context);vm.runInContext(ts.transpileModule(fn,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText,context);
  await context.train('prompt');await context.train('auto');
  assert.deepEqual(requests.map(request=>({path:request.path,body:JSON.parse(JSON.stringify(request.body))})),[{path:'/train',body:{mode:'prompt',steps:25}},{path:'/train',body:{mode:'auto',steps:25}}]);
  assert(notices.at(-1).includes('active Spot model'));
});
