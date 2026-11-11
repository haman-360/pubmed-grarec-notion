self.addEventListener('push',event=>{
 let data={};try{data=event.data?.json()||{};}catch{}
 const title=data.test?'PubMed 通知のテスト':'JSONを回収・保存しました';
 const body=data.test?'この端末で完了通知を受け取れます。':`${Number(data.count)||0}本のJSONをMacに保存しました。\n${data.at?new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',month:'long',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(data.at)):''}`;
 event.waitUntil(self.registration.showNotification(title,{body,tag:data.id||'pubmed-progress',icon:'/icon-192.png',data:{url:'/'}}));
});
self.addEventListener('notificationclick',event=>{event.notification.close();event.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(windows=>{for(const w of windows){if(new URL(w.url).origin===self.location.origin)return w.focus();}return clients.openWindow('/');}));});
