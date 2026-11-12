// Local-only visual QA. This identity adapter is never part of the deployed Worker.
import {createServer} from 'node:http';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import worker from '../dist/server/index.js';
const db=new DatabaseSync(':memory:');db.exec(readFileSync('drizzle/0000_progress.sql','utf8'));
const DB={prepare(sql){let args=[];return {bind(...v){args=v;return this;},async first(){return db.prepare(sql).get(...args)??null;},async all(){return {results:db.prepare(sql).all(...args)};},async run(){const r=db.prepare(sql).run(...args);return {meta:{changes:Number(r.changes)}};}};}};
const sample={workflow:'manual-onedrive-v1',source_id:'preview',device_name:'自宅 MacBook Air（表示確認用）',observed_at:new Date().toISOString(),busy:false,counts:{total:53,missing_pdf:42,json_pending:7,web_pending:1,review_pending:1,completed:1,attention:1},last_event:{id:'a'.repeat(64),at:new Date().toISOString(),count:5}};
db.prepare('INSERT INTO snapshots VALUES(?,?,?)').run('preview',JSON.stringify(sample),new Date().toISOString());
createServer(async(req,res)=>{const parts=[];for await(const c of req)parts.push(c);const headers={...req.headers,'oai-authenticated-user-id':'local-preview'};const request=new Request('http://127.0.0.1:8799'+req.url,{method:req.method,headers,body:['GET','HEAD'].includes(req.method)?undefined:Buffer.concat(parts)});const response=await worker.fetch(request,{DB,REPORTER_ID:'preview'});res.writeHead(response.status,Object.fromEntries(response.headers));res.end(Buffer.from(await response.arrayBuffer()));}).listen(8799,'127.0.0.1',()=>console.log('Local preview http://127.0.0.1:8799'));
