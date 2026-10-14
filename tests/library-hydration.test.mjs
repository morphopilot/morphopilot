import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import vm from 'node:vm';
import test from 'node:test';
import React from 'react';
import {renderToString} from 'react-dom/server';
import ts from 'typescript';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(path.join(root,'package.json'));
function loadPage(react=React){
  const cache=new Map();
  function load(filename){
    if(cache.has(filename))return cache.get(filename).exports;
    const module={exports:{}};cache.set(filename,module);
    const localRequire=specifier=>{
      if(specifier==='react')return react;
      if(specifier.endsWith('.css'))return {};
      if(specifier.startsWith('@/')||specifier.startsWith('.')){
        const base=specifier.startsWith('@/')?path.join(root,specifier.slice(2)):path.resolve(path.dirname(filename),specifier);
        const target=['.ts','.tsx',''].map(ext=>base+ext).find(existsSync);
        assert(target,`Resolve ${specifier}`);return load(target);
      }
      return require(specifier);
    };
    const code=ts.transpileModule(readFileSync(filename,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
    vm.runInNewContext(code,{module,exports:module.exports,require:localRequire,URLSearchParams},{filename});
    return module.exports;
  }
  return load(path.join(root,'components/data-library-page.tsx')).default;
}

test('server-rendered category select stays disabled until its navigation handler is hydrated',()=>{
  const Page=loadPage();
  const html=renderToString(React.createElement(Page,{initialWorkspace:'spot'}));
  const select=html.match(/<select[^>]*aria-label="Data library category"[^>]*>/)?.[0];
  assert(select,'The actual server render contains the category selector');
  assert.match(select,/disabled=""/);
  assert.match(html,/<option value="spot" selected="">Spot<\/option>/);
  assert.match(html,/href="\/library\?category=cell"/,'Native category links remain usable before hydration');
});

test('hydrated category selector becomes available without waiting for data or model requests',()=>{
  // Render the actual client component with initial data still loading. No effects
  // or HTTP requests run; only the hydration store has switched to its client snapshot.
  const Page=loadPage({...React,useState:initial=>[typeof initial==='function'?initial():initial,()=>{}],useRef:initial=>({current:initial}),useEffect:()=>{},useCallback:fn=>fn,useSyncExternalStore:(_subscribe,snapshot)=>snapshot()});
  const tree=Page({initialWorkspace:'spot'});
  function find(node){if(!node||typeof node!=='object')return null;if(node.type==='select'&&node.props?.['aria-label']==='Data library category')return node;for(const child of React.Children.toArray(node.props?.children)){const result=find(child);if(result)return result;}return null;}
  const select=find(tree);assert(select);assert.equal(select.props.disabled,false);
  assert.equal(select.props.value,'spot');assert.equal(typeof select.props.onChange,'function');
});
