const categories=['missing_pdf','json_pending','json_running','web_pending','completed','attention'];
const manualCategories=['missing_pdf','json_pending','web_pending','review_pending','completed','attention'];
const manual=x=>x.workflow==='manual-onedrive-v1';
const json=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
const asset=(body,type)=>new Response(body,{headers:{'Content-Type':type,'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; worker-src 'self'; frame-ancestors 'none'"}});
export function validSubscription(sub){
  if(!sub || typeof sub.endpoint!=='string' || sub.endpoint.length>2048) return false;
  try {
    const u=new URL(sub.endpoint);
    const host=u.hostname;
    if(u.protocol!=='https:' || u.port || u.username || u.password || u.hash) return false;
    if(!(host==='web.push.apple.com'||host.endsWith('.push.apple.com')||host==='fcm.googleapis.com'||host==='updates.push.services.mozilla.com'||host.endsWith('.notify.windows.com'))) return false;
    const decode=s=>Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
    const key=decode(sub.keys.p256dh),auth=decode(sub.keys.auth);
    return key.length===65 && key[0]===4 && auth.length===16;
  }catch{return false;}
}
function timestamp(x){return typeof x==='string' && x.length<=40 && Number.isFinite(Date.parse(x));}
export function validSnapshot(x,id){
  const keys=x&&manual(x)?manualCategories:categories;
  return x && x.source_id===id && typeof x.device_name==='string' && x.device_name.length<=80 &&
    (x.workflow===undefined || manual(x)) &&
    timestamp(x.observed_at) && typeof x.busy==='boolean' && x.counts &&
    ['total',...keys].every(k=>Number.isSafeInteger(x.counts[k])&&x.counts[k]>=0&&x.counts[k]<=1000000) &&
    keys.reduce((n,k)=>n+x.counts[k],0)===x.counts.total &&
    (!x.last_event || (typeof x.last_event.id==='string' && /^[a-f0-9]{64}$/.test(x.last_event.id) && timestamp(x.last_event.at) && Number.isSafeInteger(x.last_event.count) && x.last_event.count>0 && x.last_event.count<=1000000));
}
async function hash(s){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s))),b=>b.toString(16).padStart(2,'0')).join('');}
export default {async fetch(request,env){
 try {
  const url=new URL(request.url),path=url.pathname;
  // Sites enforces owner-only access before the Worker. Service access has no user identity.
  const viewer=!!request.headers.get('oai-authenticated-user-id');
  const writer=!!env.REPORTER_TOKEN && request.headers.get('X-Reporter-Token')===env.REPORTER_TOKEN;
  if(!viewer&&!writer)return json({error:'サインインが必要です'},401);
  if(request.method==='POST' || request.method==='DELETE'){
    if(!writer && request.headers.get('Origin')!==url.origin)return json({error:'許可されていない操作です'},403);
    if(Number(request.headers.get('Content-Length')||0)>16384)return json({error:'送信サイズが大きすぎます'},413);
  }
  async function body(){const text=await request.text();if(text.length>16384)throw Error('body');return JSON.parse(text);}
  if(request.method==='GET' && path==='/api/state'){
    const row=await env.DB.prepare("SELECT payload,received_at FROM snapshots WHERE id=?").bind(env.REPORTER_ID).first();
    return json({snapshot:row?JSON.parse(row.payload):null,received_at:row?.received_at??null,vapid_public_key:env.VAPID_PUBLIC_KEY??null});
  }
  if(request.method==='POST' && path==='/api/report'){
    if(!writer)return json({error:'送信専用認証が必要です'},403);
    const x=await body();if(!validSnapshot(x,env.REPORTER_ID))return json({error:'状況データが不正です'},400);
    // Allowlist the payload: never persist paper contents, local paths, or credentials.
    const keys=manual(x)?manualCategories:categories;
    const safe={...(manual(x)?{workflow:x.workflow}:{}),source_id:x.source_id,device_name:x.device_name,observed_at:x.observed_at,busy:x.busy,counts:Object.fromEntries(['total',...keys].map(k=>[k,x.counts[k]])),last_event:x.last_event?{id:x.last_event.id,at:x.last_event.at,count:x.last_event.count}:null};
    const current=await env.DB.prepare('SELECT payload FROM snapshots WHERE id=?').bind(env.REPORTER_ID).first();
    if(current && manual(JSON.parse(current.payload)) && !manual(x))return json({error:'旧版からの更新は停止しています'},409);
    await env.DB.prepare('INSERT INTO snapshots(id,payload,received_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,received_at=excluded.received_at WHERE json_extract(excluded.payload,\'$.observed_at\') > json_extract(snapshots.payload,\'$.observed_at\') AND (coalesce(json_extract(excluded.payload,\'$.workflow\'),\'\')=\'manual-onedrive-v1\' OR coalesce(json_extract(snapshots.payload,\'$.workflow\'),\'\')!=\'manual-onedrive-v1\')').bind(env.REPORTER_ID,JSON.stringify(safe),new Date().toISOString()).run();
    return json({ok:true});
  }
  if(path==='/api/subscriptions' && request.method==='GET'){
    if(!writer)return json({error:'送信専用認証が必要です'},403);
    const rows=await env.DB.prepare('SELECT * FROM subscriptions ORDER BY created_at').all();
    return json({subscriptions:rows.results.map(r=>({id:r.id,subscription:JSON.parse(r.payload),test_requested_at:r.test_requested_at}))});
  }
  if(path==='/api/subscriptions' && request.method==='POST'){
    if(!viewer)return json({error:'サインインが必要です'},403);
    const sub=await body();if(!validSubscription(sub))return json({error:'通知の登録内容が不正です'},400);
    const id=await hash(sub.endpoint);
    const old=await env.DB.prepare('SELECT id FROM subscriptions WHERE id=?').bind(id).first();
    const count=await env.DB.prepare('SELECT COUNT(*) AS n FROM subscriptions').first();
    if(!old && count.n>=12)return json({error:'登録上限です。不要な端末の通知を解除してください'},409);
    await env.DB.prepare('INSERT INTO subscriptions(id,payload,created_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload').bind(id,JSON.stringify({endpoint:sub.endpoint,keys:{p256dh:sub.keys.p256dh,auth:sub.keys.auth}}),new Date().toISOString()).run();
    return json({ok:true});
  }
  if(path==='/api/unsubscribe' && request.method==='POST'){
    const b=await body();if(typeof b.endpoint!=='string')return json({error:'不正な入力です'},400);
    await env.DB.prepare('DELETE FROM subscriptions WHERE id=?').bind(await hash(b.endpoint)).run();return json({ok:true});
  }
  if(path==='/api/test' && request.method==='POST'){
    if(!viewer)return json({error:'サインインが必要です'},403);
    const b=await body();if(typeof b.endpoint!=='string')return json({error:'不正な入力です'},400);
    const result=await env.DB.prepare('UPDATE subscriptions SET test_requested_at=? WHERE id=? AND test_requested_at IS NULL').bind(new Date().toISOString(),await hash(b.endpoint)).run();
    return json({ok:true,queued:result.meta.changes>0});
  }
  if(path==='/api/test-ack' && request.method==='POST'){
    if(!writer)return json({error:'送信専用認証が必要です'},403);
    const b=await body();if(typeof b.id!=='string'||!timestamp(b.at))return json({error:'不正な入力です'},400);
    await env.DB.prepare('UPDATE subscriptions SET test_requested_at=NULL WHERE id=? AND test_requested_at=?').bind(b.id,b.at).run();return json({ok:true});
  }
  if(request.method==='GET' && path==='/manifest.webmanifest')return asset(JSON.stringify({id:'/',name:'PubMed 進捗',short_name:'PubMed',lang:'ja',start_url:'/',scope:'/',display:'standalone',background_color:'#f5f7f6',theme_color:'#174b47',icons:[{src:'/icon-192.png',sizes:'192x192',type:'image/png',purpose:'any'},{src:'/icon-512.png',sizes:'512x512',type:'image/png',purpose:'any'}]}),'application/manifest+json');
  if(request.method==='GET' && ['/icon-192.png','/icon-512.png'].includes(path))return asset(Uint8Array.from(atob(path==='/icon-192.png'?ICON_192:ICON_512),c=>c.charCodeAt(0)),'image/png');
  if(request.method==='GET' && path==='/icon.svg')return asset('<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512"><rect width="512" height="512" rx="108" fill="#174b47"/><path d="M140 130h130a85 85 0 0 1 0 170h-65v82h-65zm65 60v50h65a25 25 0 0 0 0-50z" fill="#fff"/><circle cx="370" cy="365" r="37" fill="#bce4c3"/></svg>','image/svg+xml');
  if(request.method==='GET' && path==='/sw.js')return asset(SW,'application/javascript');
  if(request.method==='GET' && path==='/client.js')return asset(CLIENT,'application/javascript');
  if(request.method==='GET' && path==='/')return asset(HTML,'text/html; charset=utf-8');
  return json({error:'ページが見つかりません'},404);
 }catch{return json({error:'読み込みに失敗しました。しばらくしてから再試行してください。'},500);}
}};
