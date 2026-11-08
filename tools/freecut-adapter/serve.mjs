// Local, separate-origin FreeCut host. Never serves Studio credentials or arbitrary paths.
import http from 'node:http';
import {agentChannel} from './agent-channel.mjs';
const channel=agentChannel();
import {readFile,stat} from 'node:fs/promises';
import {resolve,extname} from 'node:path';
const root=resolve(import.meta.dirname,'../freecut-upstream/dist');
const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml','.png':'image/png','.wasm':'application/wasm','.woff2':'font/woff2','.mp4':'video/mp4'};
http.createServer(async(req,res)=>{
 try {
  const bridgeUrl=new URL(req.url,'http://127.0.0.1:18189');
  if(await channel(req,res,bridgeUrl))return;
  if(!['GET','HEAD'].includes(req.method)){res.writeHead(405).end();return;}
  const url=new URL(req.url,'http://127.0.0.1:18189');
  if(url.pathname.startsWith('/api/')){
   if(!/^\/api\/(montages\/media|assets\/[a-zA-Z0-9-]+\/(file|thumbnail)|jobs\/[a-zA-Z0-9-]+\/(video|poster))$/.test(url.pathname)){res.writeHead(403).end();return;}
   const response=await fetch('http://127.0.0.1:18187'+url.pathname,{headers:req.headers.range?{range:req.headers.range}:{},signal:AbortSignal.timeout(30000)});
   res.writeHead(response.status,Object.fromEntries([...response.headers].filter(([k])=>['content-type','content-length','content-range','accept-ranges','content-disposition'].includes(k))));
   if(req.method==='HEAD'){res.end();return;}
   for await(const chunk of response.body) {if(res.destroyed)break;res.write(chunk);}res.end();return;
  }
  let file=resolve(root,'.'+decodeURIComponent(url.pathname));if(file!==root&&!file.startsWith(root+'/')){res.writeHead(403).end();return;}
  try{if(!(await stat(file)).isFile())file=resolve(root,'index.html');}catch{file=resolve(root,'index.html');}
  const data=await readFile(file);res.writeHead(200,{'Content-Type':mime[extname(file)]||'application/octet-stream','Cache-Control':'no-cache'});res.end(req.method==='HEAD'?undefined:data);
 }catch{res.writeHead(502).end('FreeCut build or Studio media service unavailable');}
}).listen(18189,'127.0.0.1',()=>console.log('TaleMondo FreeCut: http://127.0.0.1:18189/projects'));
