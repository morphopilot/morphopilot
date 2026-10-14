import assert from 'node:assert/strict';
import {createServer as createHttpServer} from 'node:http';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import {createServer as createViteServer} from 'vite';

test('standalone development keeps password sessions, category paths and mutation origin on the local gateway',async()=>{
  const calls=[];
  // Contract fixture only: no neural inference, application data or real token.
  const gateway=createHttpServer(async(req,res)=>{
    let body='';for await(const chunk of req)body+=chunk;
    calls.push({url:req.url,method:req.method,host:req.headers.host,origin:req.headers.origin,cookie:req.headers.cookie,authorization:req.headers.authorization,body});
    if(req.url==='/login'&&req.method==='POST'){res.writeHead(200,{'Set-Cookie':'morphopilot_session=test-session; HttpOnly; SameSite=Lax; Path=/'});res.end('signed in');return;}
    if(req.headers.cookie!=='morphopilot_session=test-session'){res.writeHead(401);res.end('Authentication required');return;}
    if(req.method==='POST'&&req.headers.origin!==`http://${req.headers.host}`){res.writeHead(403);res.end('Invalid origin');return;}
    res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({path:req.url,body}));
  });
  gateway.listen(0,'127.0.0.1');await once(gateway,'listening');
  const old=process.env.MORPHOPILOT_DEV_GATEWAY;
  process.env.MORPHOPILOT_DEV_GATEWAY=`http://127.0.0.1:${gateway.address().port}`;
  let vite;
  try{
    vite=await createViteServer({configFile:fileURLToPath(new URL('../vite.standalone.config.ts',import.meta.url)),server:{host:'127.0.0.1',port:0},logLevel:'silent'});
    await vite.listen();const origin=`http://127.0.0.1:${vite.httpServer.address().port}`;
    assert.equal((await fetch(`${origin}/api/gpu/categories/spot/state`)).status,401);
    const login=await fetch(`${origin}/login`,{method:'POST',body:'password=fixture',headers:{Origin:origin,'Content-Type':'application/x-www-form-urlencoded'}});
    assert.equal(login.status,200);assert.match(login.headers.get('set-cookie'),/HttpOnly/);
    const cookie=login.headers.get('set-cookie').split(';')[0];
    const body=JSON.stringify({tool:'point',points:[[0,4,5]],revision:3});
    const response=await fetch(`${origin}/api/gpu/categories/spot/samples/test/interaction?mode=prompt`,{method:'POST',body,headers:{Cookie:cookie,Origin:origin,'Content-Type':'application/json'}});
    assert.equal(response.status,200);assert.deepEqual(await response.json(),{path:'/api/gpu/categories/spot/samples/test/interaction?mode=prompt',body});
    assert.equal((await fetch(`${origin}/auth/logout`,{method:'POST',headers:{Cookie:cookie,Origin:origin}})).status,200);
    assert.equal((await fetch(`${origin}/api/gpu/categories/spot/train`,{method:'POST',body:'{}',headers:{Cookie:cookie,Origin:'http://evil.invalid'}})).status,403);
    assert(calls.every(call=>!call.authorization),'The frontend dev proxy must never inject an API bearer credential');
  }finally{
    await vite?.close();await new Promise(resolve=>gateway.close(resolve));
    if(old===undefined)delete process.env.MORPHOPILOT_DEV_GATEWAY;else process.env.MORPHOPILOT_DEV_GATEWAY=old;
  }
});
