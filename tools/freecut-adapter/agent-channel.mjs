// Loopback-only bridge; one active editor tab owns a lease. No arbitrary JS or file access.
import {randomUUID} from 'node:crypto';
export function agentChannel(){
 let lease='',seen=0,pending=null;const results=new Map();let diagnostic=null;
 return async(req,res,url)=>{
  if(!url.pathname.startsWith('/bridge/'))return false;
  const origin=req.headers.origin;
  if(req.method==='POST'&&!String(req.headers['content-type']||'').startsWith('application/json')){res.writeHead(415).end();return true;}
  if(req.headers.host!=='127.0.0.1:18189'||(origin&&origin!=='http://127.0.0.1:18189')){res.writeHead(403).end();return true;}
  const reply=(status,data)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
  let data={};if(req.method==='POST'){let raw='';for await(const part of req){raw+=part;if(raw.length>262144){reply(413,{error:'Too large'});return true;}}try{data=JSON.parse(raw);}catch{reply(400,{error:'Invalid JSON'});return true;}}
  if(url.pathname==='/bridge/error'&&req.method==='POST'){diagnostic=String(data.error||'').slice(0,12000);reply(200,{ok:true});return true;}
  if(url.pathname==='/bridge/status'){reply(200,{connected:Date.now()-seen<10000,error:diagnostic});return true;}
  if(url.pathname==='/bridge/poll'&&req.method==='POST'){
   if(data.lease!==lease&&Date.now()-seen<10000){reply(409,{error:'Another tab owns editor'});return true;}
   if(!lease||Date.now()-seen>=10000)lease=randomUUID();seen=Date.now();
   reply(200,{lease,command:pending});return true;
  }
  if(url.pathname==='/bridge/result'&&req.method==='POST'){
   if(data.lease!==lease){reply(403,{});return true;}
   if(pending?.id===data.id){results.set(data.id,data.result);pending=null;while(results.size>100)results.delete(results.keys().next().value);}reply(200,{ok:true});return true;
  }
  if(url.pathname==='/bridge/command'&&req.method==='POST'){
   if(Date.now()-seen>=10000){reply(503,{error:'Open the FreeCut editor first'});return true;}
   if(pending){reply(409,{error:'A command is already pending'});return true;}
   if(!['timeline_get','timeline_edit'].includes(data.action)){reply(422,{error:'Unsupported action'});return true;}
   pending={...data,id:randomUUID()};reply(202,{id:pending.id});return true;
  }
  if(url.pathname.startsWith('/bridge/result/')&&req.method==='GET'){const id=url.pathname.split('/').pop();reply(results.has(id)?200:202,results.get(id)||{pending:true});return true;}
  reply(404,{});return true;
 };
}
