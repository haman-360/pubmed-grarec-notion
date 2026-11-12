const $=id=>document.getElementById(id);
const legacyFields=[['missing_pdf','PDF取得待ち'],['json_pending','JSON作成待ち'],['json_running','JSON作成・回収待ち'],['web_pending','画像作成・登録待ち'],['completed','Notion登録完了'],['attention','要確認・保留']];
const manualFields=[['missing_pdf','PDF取得待ち'],['json_pending','JSON作成待ち'],['web_pending','PNG作成待ち'],['review_pending','PNG確認・手動登録待ち'],['completed','手動登録完了'],['attention','要確認・競合']];
let fields=manualFields,workflow='';
function renderMetrics(next){fields=next;$('metrics').replaceChildren();for(const [key,label] of fields){const box=document.createElement('div');box.className='metric';const name=document.createElement('span');name.textContent=label;const num=document.createElement('strong');num.id=key;num.textContent='—';box.append(name,num);$('metrics').append(box);}}
renderMetrics(fields);
const format=s=>new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',month:'long',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(s));
let config=null,reg=null,loading=false;
async function api(path,data){const r=await fetch(path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data),cache:'no-store'});if(!r.ok||!r.headers.get('content-type')?.includes('application/json'))throw Error('接続できません。ページを開き直してサインインを確認してください。');return r.json();}
function alertMessage(s){$('alert').textContent=s;$('alert').hidden=!s;}
async function refresh(){if(loading)return;loading=true;try{config=await api('/api/state');const s=config.snapshot;if(!s){$('health').textContent='Macからの初回同期を待っています';alertMessage('Macで状況共有の設定が完了すると、ここに件数が表示されます。');return;}
const manual=s.workflow==='manual-onedrive-v1';
if(workflow!==(s.workflow||'legacy')){workflow=s.workflow||'legacy';renderMetrics(manual?manualFields:legacyFields);}
$('latest-panel').hidden=manual;$('notification-panel').hidden=manual;
$('total').textContent=s.counts.total;for(const [k] of fields)$(k).textContent=s.counts[k];$('device').textContent='確認端末：'+s.device_name;$('updated').textContent='Macでの最終確認 '+format(s.observed_at);
const stale=Date.now()-Date.parse(s.observed_at)>2*60*1000;
$('health').textContent=stale?'最後に共有された状況':manual?'Macと同じ分類で共有しています':s.busy?'Macで処理中':s.counts.json_running?'JSONの作成・回収待ち':'現在、JSONの作成・回収待ちはありません';
alertMessage(stale?'Macからの共有が2分以上更新されていません。アプリ終了・スリープ・通信状況によっては、最後に共有した件数を表示します。':manual?'':'旧方式の記録です。Macで最新版のアプリを開くと、OneDrive版の状況に切り替わります。');
if(s.last_event){$('event').textContent=s.last_event.count+'本のJSONを回収・保存しました';$('event-time').textContent=format(s.last_event.at)+' ／ '+s.device_name;}
if(reg&&!manual)await notificationState();
}catch(e){alertMessage(e.message);}finally{loading=false;}}
function decode(s){const b=atob(s.replace(/-/g,'+').replace(/_/g,'/'));return Uint8Array.from(b,c=>c.charCodeAt(0));}
async function notificationState(){const sub=await reg.pushManager.getSubscription();$('enable').hidden=!!sub;$('enable').disabled=!config?.vapid_public_key;$('test').hidden=!sub;$('disable').hidden=!sub;}
async function initPush(){if(!('serviceWorker' in navigator)||!('PushManager' in window)||!('Notification' in window)){$('notification-guide').textContent='このブラウザーでは通知を設定できません。iPhone・iPadではSafariからホーム画面に追加して開いてください。';return;}try{reg=await navigator.serviceWorker.register('/sw.js');await navigator.serviceWorker.ready;await notificationState();}catch{$('feedback').textContent='通知の準備ができませんでした。ページを開き直してください。';}}
$('enable').onclick=async()=>{try{const permission=await Notification.requestPermission();if(permission!=='granted')throw Error('通知は許可されていません。端末の設定から許可してください。');const sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:decode(config.vapid_public_key)});try{await api('/api/subscriptions',sub.toJSON());}catch(e){await sub.unsubscribe();throw e;}$('feedback').textContent='通知を有効にしました。「テスト通知」で動作を確認できます。';await notificationState();}catch(e){$('feedback').textContent=e.message;}};
$('disable').onclick=async()=>{try{const sub=await reg.pushManager.getSubscription();if(sub){await api('/api/unsubscribe',{endpoint:sub.endpoint});await sub.unsubscribe();}$('feedback').textContent='この端末の通知を解除しました。';await notificationState();}catch(e){$('feedback').textContent=e.message;}};
$('test').onclick=async()=>{try{const sub=await reg.pushManager.getSubscription();if(!sub)throw Error('先に通知を有効にしてください。');await api('/api/subscriptions',sub.toJSON());await api('/api/test',{endpoint:sub.endpoint});$('feedback').textContent='テストを予約しました。Macアプリを開いていれば約1分で届きます。';}catch(e){$('feedback').textContent=e.message;}};
$('refresh').onclick=refresh;
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
setInterval(()=>{if(!document.hidden)refresh();},30000);
refresh().then(()=>{if(config?.snapshot?.workflow!=='manual-onedrive-v1')initPush();});
