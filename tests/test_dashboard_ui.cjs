// Offline checks of the actual dashboard's filtering and link rendering logic.
// Run with: node --test tests/test_dashboard_ui.cjs
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../scripts/dashboard.html'),'utf8').split('<script>')[1].split('</script>')[0];
function dashboard(){
    const nodes=new Map();
    const context=vm.createContext({URL,fetch:()=>new Promise(()=>{}),document:{
        getElementById(id){if(!nodes.has(id))nodes.set(id,{value:'',hidden:false});return nodes.get(id)},
        createElement(){return {}}
    }});
    vm.runInContext(script,context);
    const run=source=>JSON.parse(JSON.stringify(vm.runInContext(source,context)));
    const set=(source)=>vm.runInContext(source,context);
    set(`state={papers:[
        {pmid:'1',title:'First',category:'missing_pdf',heads:['a'],files:[]},
        {pmid:'2',title:'Second',category:'missing_pdf',heads:['b'],files:[]},
        {pmid:'3',title:'Third',category:'missing_pdf',heads:['c'],files:[]},
        {pmid:'4',title:'Fourth',category:'completed',heads:['d'],files:[]}],
        pdf_search_rows:[
            {pmid:'1',title:'First',method:'free',difficulty:'medium',at:'2026-11-01'},
            {pmid:'1',title:'First',method:'paid',difficulty:'high',at:'2026-11-14'},
            {pmid:'2',title:'Second',method:'free',difficulty:'low',at:'2026-11-02'},
            {pmid:'4',title:'Fourth',method:'free',difficulty:'done',at:'2026-11-03'}]};
        view='missing_pdf';$('pdf-history-method').value='all';$('pdf-history-sort').value='easy';`);
    return {run,set};
}
test('PDF stage groups free/paid per PMID, includes unsearched and excludes acquired papers',()=>{
    const {run}=dashboard();assert.deepEqual(run('paperRows().map(p=>p.pmid)'),['2','1','3']);
});
test('method, unsearched and title filters compose without duplicating papers',()=>{
    const {run,set}=dashboard();set("$('pdf-history-method').value='paid'");assert.deepEqual(run('paperRows().map(p=>p.pmid)'),['1']);
    set("$('pdf-history-method').value='unsearched'");assert.deepEqual(run('paperRows().map(p=>p.pmid)'),['3']);
    set("$('pdf-history-method').value='all';$('find').value='SECOND'");assert.deepEqual(run('paperRows().map(p=>p.pmid)'),['2']);
});
test('history includes acquired papers once, and recent order uses latest method result',()=>{
    const {run,set}=dashboard();set("view='history';$('pdf-history-sort').value='recent'");assert.deepEqual(run('paperRows().map(p=>p.pmid)'),['1','4','2']);
});
test('all-paper and individual stages remain separate and empty searches return no rows',()=>{
    const {run,set}=dashboard();set("view='total'");assert.equal(run('paperRows().length'),4);
    set("view='completed'");assert.deepEqual(run('paperRows().map(p=>p.pmid)'),['4']);
    set("$('find').value='missing'");assert.deepEqual(run('paperRows()'),[]);
});
test('submitted JSON cannot be selected again for the same PDF but a new source can',()=>{
    const {run,set}=dashboard();set(`state.papers=[{pmid:'1',category:'json_pending',heads:['a'],files:[{kind:'pdf',sha:'old'}]},{pmid:'2',category:'json_pending',heads:['b'],files:[{kind:'pdf',sha:'new'}]}];state.json_batch={jobs:[{pmid:'1',source_hash:'old',attempts:[{status:'submitted'}]},{pmid:'2',source_hash:'old',attempts:[{status:'submitted'}]}]}`);
    assert.deepEqual(run('jsonRows().map(p=>p.pmid)'),['2']);
});
test('Batch columns render type and source separately and retain the selected paper after metadata refresh',async()=>{
    const {run,set}=dashboard();
    set(`
        class TestNode {
            constructor(tag){this.tag=tag;this.children=[];this.dataset={}}
            append(...children){this.children.push(...children)}
            replaceChildren(...children){this.children=children}
        }
        var nodes=new Map();
        document.getElementById=id=>{if(!nodes.has(id))nodes.set(id,new TestNode('div'));return nodes.get(id)};
        document.createElement=tag=>new TestNode(tag);
        var paper={pmid:'42',heads:['a'],title:'A lengthy title: A Narrative Review',category:'json_pending',files:[],
            publication_type:'ナラティブレビュー',publication_type_inferred:true,publication_types:[]};
        state={busy:false,papers:[paper],json_batch:{is_owner:true,jobs:[]}};
        var calls=[];act=async(action,payload)=>{calls.push({action,payload})};
        renderJsonPanel();
        var cells=()=>$('json-option-rows').children[0].children;
        var check=cells()[0].children[0];
        check.checked=true;check.onchange();
    `);
    assert.equal(run('cells().length'),4);
    assert.equal(run('cells()[1].textContent'),'42');
    assert.equal(run('cells()[2].children[0].htmlFor'),run('check.id'));
    assert.equal(run('cells()[3].children[0].textContent'),'ナラティブレビュー');
    assert.equal(run('cells()[3].children[1].textContent'),'タイトルから推定');
    assert.equal(run('check===cells()[0].children[0]'),true);
    await set('updateJsonMetadata()');
    assert.deepEqual(run('calls'),[{action:'json-metadata',payload:{pmids:['42']}}]);
    set("paper.publication_type_inferred=false;paper.publication_types=['Narrative Review'];renderJsonPanel()");
    assert.equal(run('cells()[0].children[0].checked'),true);
    assert.equal(run('cells()[3].children[1].textContent'),'PubMed · Narrative Review');
});
test('an old held group can rebuild its estimate in one click before confirming send',async()=>{
    const {run,set}=dashboard();
    set(`
        class TestNode {
            constructor(){this.children=[];this.dataset={};this.checked=false;this.value=''}
            append(...children){this.children.push(...children)}
            replaceChildren(...children){this.children=children}
        }
        var uiNodes=new Map();
        document.getElementById=id=>{if(!uiNodes.has(id))uiNodes.set(id,new TestNode());return uiNodes.get(id)};
        document.createElement=()=>new TestNode();document.createTextNode=text=>({textContent:text});
        var calls=[];
        act=(action,payload)=>calls.push({action,payload});
        state={busy:false,papers:[{pmid:'1',heads:['a'],title:'Review',publication_type:'レビュー',category:'json_pending',files:[]}],
            json_batch:{is_owner:true,key_ready:true,jobs:[],preview:{id:'hold',policy:1,items:[],total:0,
                held:[{pmid:'1',heads:['a'],title:'Review',publication_type:'レビュー',reason:'長文',can_estimate_full:true}]}}};
        state.papers.push({...state.papers[0],pmid:'2',heads:['b']});
        state.json_batch.preview.held.push({pmid:'2',heads:['b']});
        renderJsonPanel();
    `);
    assert.equal(run("$('json-send').disabled"),true);
    assert.equal(run("$('json-preview').hidden"),true);
    assert.equal(run("$('json-held').hidden"),false);
    set("$('json-held').children[1].onclick()");
    assert.deepEqual(run('calls'),[{action:'json-prepare',payload:{items:[{pmid:'1',heads:['a']},{pmid:'2',heads:['b']}]}}]);
    set(`state.json_batch.preview={id:'fresh',policy:2,items:state.papers.map(p=>({...p,reserve:0.75,needs_count:true})),total:1.5};renderJsonPanel()`);
    assert.equal(run("$('json-estimate-rows').children.length"),2);
    assert.equal(run("$('json-estimate-count-note').hidden"),false);
    assert.equal(run("$('json-send').disabled"),true);
    set("$('json-send-confirm').checked=true;renderJsonPanel()");
    assert.equal(run("$('json-send').disabled"),false);
});
test('manual links keep full destinations but use short labels and reject executable URLs',()=>{
    const {run,set}=dashboard();set("var links=[];var parent={append:a=>links.push(a)};manualLink(parent,'https://doi.org/10.1000/test');manualLink(parent,'https://example.org/'+ 'long-title-'.repeat(50));manualLink(parent,'javascript:alert(1)');manualLink(parent,'not a URL')");
    const links=run('links');assert.equal(links.length,2);assert.equal(links[0].textContent,'出版社（DOI）');assert.equal(links[0].href,'https://doi.org/10.1000/test');assert.equal(links[1].textContent,'example.org');assert.ok(links[1].href.length>500);assert.equal(links[1].rel,'noopener noreferrer');
});
test('retry screen shows preserved cost, limits, explicit consent and owner/busy locks',async()=>{
    const {run,set}=dashboard();
    set(`
        class TestNode {
            constructor(){this.children=[];this.style={};this.attributes={};this.checked=false;this.value=''}
            append(...children){this.children.push(...children)}
            replaceChildren(...children){this.children=children}
            setAttribute(k,v){this.attributes[k]=v}
            scrollIntoView(){}
        }
        var uiNodes=new Map();
        document.getElementById=id=>{if(!uiNodes.has(id))uiNodes.set(id,new TestNode());return uiNodes.get(id)};
        document.createElement=()=>new TestNode();document.createTextNode=text=>({textContent:text});
        document.querySelectorAll=()=>($('json-jobs').children.flatMap(box=>box.children).filter(n=>n.attributes&&'data-json-retry' in n.attributes));
        state={busy:false,papers:[],json_batch:{is_owner:true,key_ready:true,jobs:[{id:'revision-1',pmid:'1',status:'review_required',message:'要確認',attempts:[{status:'review_required',batch_id:'batch-original',reserve:.325397,actual:null}],retry:{allowed:true,used:0,limit:1,cap:1,reason:'HTTP 503'}}]}};
        renderJsonPanel();
    `);
    assert.equal(run("document.querySelectorAll()[0].disabled"),false);
    assert.equal(run("$('json-retry-send').disabled"),true);
    assert.equal(run("$('json-retry-preview').hidden"),true);
    set(`var recorded=[];act=async(action,data)=>{recorded.push({action,data});return true}`);
    await set("document.querySelectorAll()[0].onclick()");
    assert.deepEqual(run('recorded'),[{action:'json-retry-prepare',data:{revision_id:'revision-1'}}]);
    set(`state.json_batch.retry_preview={id:'preview-1',item:{pmid:'1',title:'Test paper',reason:'HTTP 503',number:1,limit:1,previous:.325397,reserve:.325397,total:.650794,cap:1}};renderJsonPanel()`);
    assert.equal(run("$('json-retry-preview').hidden"),false);
    assert.equal(run("$('json-retry-pmid').textContent"),'PMID 1');
    assert.equal(run("$('json-retry-title').textContent"),'Test paper');
    assert.equal(run("$('json-retry-reason').textContent"),'HTTP 503');
    assert.equal(run("$('json-retry-count').textContent"),'1 / 1回（初回を含め計2回まで）');
    assert.equal(run("$('json-retry-previous').textContent"),'$0.325397');
    assert.equal(run("$('json-retry-reserve').textContent"),'$0.325397');
    assert.equal(run("$('json-retry-total').textContent"),'$0.650794');
    assert.equal(run("$('json-retry-cap').textContent"),'$1.00');
    assert.equal(run("$('json-retry-send').disabled"),true);
    set("$('json-retry-confirm').checked=true;renderJsonPanel()");
    assert.equal(run("$('json-retry-send').disabled"),false);
    set('state.busy=true;renderJsonPanel()');
    assert.equal(run("$('json-retry-send').disabled"),true);
    assert.equal(run('document.querySelectorAll()[0].disabled'),true);
    set('state.busy=false;state.json_batch.is_owner=false;renderJsonPanel()');
    assert.equal(run("$('json-retry-send').disabled"),true);
    assert.equal(run('document.querySelectorAll()[0].disabled'),true);
    set('state.json_batch.is_owner=true;state.json_batch.retry_preview.id="preview-2";renderJsonPanel()');
    assert.equal(run("$('json-retry-confirm').checked"),false);
    set("$('json-retry-confirm').checked=true;renderJsonPanel()");
    await set('sendJsonRetry()');
    assert.deepEqual(run('recorded[1]'),{action:'json-retry-send',data:{preview_id:'preview-2',confirmed:true}});
    assert.equal(run("$('json-retry-confirm').checked"),false);
    set('state.json_batch.jobs[0].retry={allowed:false,used:1,limit:1};renderJsonPanel()');
    assert.equal(run('document.querySelectorAll().length'),0);
});

test('archived and deferred papers stay out of active search; due papers lead all sort orders',()=>{
    const {run,set}=dashboard();
    set("state.papers[0].category='archived';state.papers[1].category='deferred';state.papers[2].recheck_due=true;state.papers.push({pmid:'5',title:'New',category:'missing_pdf',heads:['e'],files:[]})");
    assert.deepEqual(run('pdfRows().map(p=>p.pmid)'),['3','5']);
    for(const sort of ['easy','hard','recent']){
        set(`$('pdf-history-sort').value='${sort}'`);
        assert.deepEqual(run('paperRows().map(p=>p.pmid)'),['3','5']);
    }
    set("view='archived'");assert.deepEqual(run('paperRows().map(p=>p.pmid)'),['1']);
    set("view='total'");assert.equal(run('paperRows().length'),5);
});

test('waiting list sorts by next check date independently of search history filters',()=>{
    const {run,set}=dashboard();
    set("state.papers[0].category='deferred';state.papers[0].pdf_recheck={next_on:'2027-01-01'};state.papers[1].category='deferred';state.papers[1].pdf_recheck={next_on:'2026-12-16'};view='deferred';$('pdf-history-method').value='paid'");
    assert.deepEqual(run('paperRows().map(p=>p.pmid)'),['2','1']);
});

test('a waiting Batch can archive its current paper without API ownership',async()=>{
    const {run,set}=dashboard();
    set(`
        document.createElement=tag=>({tag,dataset:{},children:[],append(child){this.children.push(child)}});
        const jobsNode=$('json-jobs');
        jobsNode.append=child=>jobsNode.children.push(child);
        jobsNode.replaceChildren=()=>{jobsNode.children=[]};
        $('management-panel').scrollIntoView=()=>{};$('management-title').focus=()=>{};
        var recorded=[];act=async(action,data)=>{recorded.push({action,data});return true};
        const paper=state.papers[0];
        Object.assign(paper,{category:'json_pending',files:[{kind:'pdf',sha:'current'}]});
        const jobs=[{pmid:paper.pmid,source_hash:'current',status:'budget_wait',attempts:[],message:'長文料金域'},
                    {pmid:paper.pmid,source_hash:'old',status:'budget_wait',attempts:[],message:'過去の処理'}];
        const archiveButton=()=>jobsNode.children[0].children.find(child=>child.tag==='button');
        renderJsonJobs(jobs,true);
    `);
    set("jobs[0].web_json=true;state.json_batch={jobs};renderJsonJobs(jobs,true)");
    assert.equal(run('jobsNode.children[0].open'),true);
    assert.deepEqual(run('jsonRows().map(p=>p.pmid)'),[]);
    set("jobsNode.children[0].children.find(n=>n.textContent==='依頼文をコピー（PDF添付）').onclick()");
    assert.deepEqual(run('recorded.pop()'),{action:'copy-json',data:{pmid:'1',heads:['a']}});
    set("jobs[0].web_json=false;renderJsonJobs(jobs,true)");
    assert.equal(run('archiveButton().disabled'),false);
    assert.equal(run("jobsNode.children[1].children.some(child=>child.tag==='button')"),false);
    set("archiveButton().onclick();$('archive-reason').value='長文のため'");
    await set('saveManagement()');
    assert.deepEqual(run('recorded'),[{action:'manage-paper',data:{pmid:'1',heads:['a'],operation:'archive',reason:'長文のため',note:''}}]);
    set("paper.category='archived';paper.archive={reason:'長文のため'};renderJsonJobs(jobs,true)");
    assert.equal(run('jobsNode.children[0].children[0].textContent'),'PMID 1 ／ アーカイブ済み');
    assert.match(run('jobsNode.children[0].children[1].textContent'),/長文のため/);
    assert.equal(run('Boolean(archiveButton())'),false);
    assert.deepEqual(run('jsonRows().map(p=>p.pmid)'),[]);
});

test('management form defaults to calendar month, sends edited date, and reschedules without replacing the check',async()=>{
    const {run,set}=dashboard();
    set(`state.today='2026-11-16';state.default_recheck_on='2026-12-16';
        $('management-panel').scrollIntoView=()=>{};$('management-title').focus=()=>{};
        var recorded=[];act=async(action,data)=>{recorded.push({action,data});return true};
        openManagement(state.papers[0],'defer');`);
    assert.equal(run("$('next-check-on').value"),'2026-12-16');
    assert.equal(run("$('check-on').value"),'2026-11-16');
    set("$('next-check-on').value='2027-01-20';$('management-note').value='Publisher inaccessible'");
    await set('saveManagement()');
    assert.deepEqual(run('recorded[0]'),{action:'manage-paper',data:{pmid:'1',heads:['a'],operation:'defer',next_on:'2027-01-20',checked_on:'2026-11-16',result:'有料のみ',note:'Publisher inaccessible'}});
    set("state.papers[0].pdf_recheck={next_on:'2027-01-20'};openManagement(state.papers[0],'reschedule');$('next-check-on').value='2027-02-01'");
    await set('saveManagement()');
    assert.deepEqual(run('recorded[1].data'),{pmid:'1',heads:['a'],operation:'reschedule',next_on:'2027-02-01'});
    set("openManagement(state.papers[0],'archive');$('archive-reason').value='古い'");
    await set('saveManagement()');
    assert.deepEqual(run('recorded[2].data'),{pmid:'1',heads:['a'],operation:'archive',reason:'古い',note:''});
});

test('Notion lists only papers with all medical reviews approved',()=>{
    const {run,set}=dashboard();
    set("state.papers[0].category='review_pending';state.papers[1].category='review_pending';state.papers[1].medically_approved=true");
    assert.deepEqual(run('notionRows().map(p=>p.pmid)'),['2']);
});

test('PNG plan shows the overview, each topic title and the count for older files',()=>{
    const {run,set}=dashboard();
    set(`
        document.createElement=()=>({children:[],style:{},append(child){this.children.push(child)}});
        const parent=document.createElement();
        const paper={pmid:'42',heads:['a'],files:[
            {kind:'json',plan_reason:'対象集団ごとに分ける'},
            {kind:'graphic',topic:'overview',sha:'o'},{kind:'graphic',topic:'a',topic_title:'診断',sha:'a'},
            {kind:'graphic',topic:'b',topic_title:'治療',sha:'b'}]};
        pngActions(parent,paper);
    `);
    assert.equal(run("parent.children[0].textContent"),'グラレコ 全3枚（全体図1枚＋テーマ別2枚） ／ 0枚保存済み');
    assert.match(run('parent.children[1].textContent'),/対象集団ごとに分ける/);
    assert.deepEqual(run('parent.children[2].children.map(box=>box.children[0].textContent)'),
        ['全体図 ／ 作成待ち','診断 ／ 作成待ち','治療 ／ 作成待ち']);
    set("paper.files=[{kind:'graphic',topic:'main',sha:'one'}];const old=document.createElement();pngActions(old,paper)");
    assert.equal(run('old.children[0].textContent'),'グラレコ 全1枚 ／ 0枚保存済み');
    assert.equal(run('old.children[1].children[0].children[0].textContent'),'main ／ 作成待ち');
});
test('teaching HTML can be registered before any PNG exists',()=>{
    const {run,set}=dashboard();
    set("state={busy:false,materials:{version:'edition',requests:5},notion_sync:{key_ready:true,can_operate:true}};renderMaterials()");
    assert.equal(run("$('materials-register').hidden"),false);
    assert.equal(run("$('materials-register').disabled"),false);
    set("var calls=[];act=(action,data)=>calls.push({action,data});materialAction('register')");
    assert.deepEqual(run('calls'),[{action:'materials-register',data:{version:'edition'}}]);
    set('state.materials={};renderMaterials()');
    assert.equal(run("$('materials-register').hidden"),true);
});

test('teaching stage selects registered papers across searches and drops stale selections',async()=>{
    const {run,set}=dashboard();
    set(`
        const nodes=new Map();
        class Element {
            constructor(){this.children=[];this.dataset={};this.value=''}
            append(el){this.children.push(el);el.parent=this;if(el.id)nodes.set(el.id,el)}
            replaceChildren(){this.children=[]}
            closest(){return this.parent.parent}
        }
        document.createElement=()=>new Element();
        document.getElementById=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id)};
        $('teaching-batch-route').value='master';
        state.teaching_jobs=[{pmid:'3'}];
        state.papers[3].files=[{kind:'pdf',name:'paper.pdf'}];
        state.papers.push({...state.papers[0],pmid:'5',category:'archived'});
        state.papers.push({...state.papers[3],pmid:'6',missing:['paper.pdf']});
        state.papers.push({...state.papers[3],pmid:'7',heads:['c','d']});
        state.papers.push({...state.papers[3],pmid:'42526604'});
        state.materials={version:'legacy'};
        var calls=[];act=async(action,data)=>calls.push({action,data});
        renderTeachingOptions();
    `);
    assert.deepEqual(run('teachingRows().map(p=>p.pmid)'),['1','2','4']);
    assert.equal(run("$('teaching-submit').disabled"),true);
    set("$('teaching-find').value='FIRST';selectTeachingRows();$('teaching-find').value='fourth';selectTeachingRows()");
    assert.equal(run("$('teaching-count').textContent"),'2本選択中');
    set("$('teaching-find').value='';state.papers[0].heads=['updated'];renderTeachingOptions()");
    assert.equal(run("$('teach-1').checked"),false);
    set("$('teach-2').checked=true;$('teach-2').onchange();$('teaching-batch-notion').checked=true");
    await set('startSelectedTeaching()');
    assert.deepEqual(run('calls'),[{action:'teaching-request-selected',data:{
        items:[{pmid:'4',heads:['d']},{pmid:'2',heads:['b']}],route:'master',paid:true,notion:true}}]);
    set(`state.papers[2].files=[{kind:'json',name:'summary.json',sha:'json-3'}];
        $('teaching-batch-route').value='existing';teachingSelection.clear();selectTeachingRows()`);
    assert.deepEqual(run('teachingRows().map(p=>p.pmid)'),['3']);
    await set('startSelectedTeaching()');
    assert.deepEqual(run('calls[1].data'),{items:[{pmid:'3',heads:['c']}],route:'existing',paid:false,notion:true});
    set("state.teaching_jobs.push({pmid:'3',route:'existing',source_sha:'json-3'})");
    assert.deepEqual(run('teachingRows()'),[]);
});

test('teaching history links each registered edition to its page and keeps pending settings accessible',()=>{
    const {run,set}=dashboard();
    set(`
        const element=tag=>({tag,children:[],dataset:{},append(child){this.children.push(child)}});
        document.createElement=element;
        const jobs=element('div');
        $('teaching-jobs').append=child=>jobs.append(child);
        $('teaching-papers').append=()=>{};
        $('teaching-single').scrollIntoView=()=>{};
        renderMaterials=()=>{};
        const id='11111111-2222-3333-4444-555555555555';
        state.teaching_jobs=[{pmid:'1',route:'existing',version:'v1',page_id:id,notion:true},
            {pmid:'2',message:'精読結果待ち',paid:true}];
        state.materials={version:'v2',page_id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'};
        renderTeachingJobs();
        const actions=jobs.children.map(row=>row.children[1].children);
    `);
    assert.deepEqual(run("actions.map(nodes=>nodes.map(n=>n.tag))"),[['a','button'],['button'],['a','button']]);
    assert.equal(run("actions[0][0].href"),'https://www.notion.so/11111111222233334444555555555555');
    assert.equal(run("actions[2][0].href"),'https://www.notion.so/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    assert.equal(run("jobs.children[0].children[0].children[2].textContent"),'教材作成済み · Notion登録済み');
    assert.equal(run("jobs.children[1].children[0].children[2].textContent"),'精読結果待ち');
    set('actions[0][1].onclick()');
    assert.equal(run("$('teaching-pmid').value"),'1');
    assert.equal(run("$('teaching-route').value"),'existing');
    assert.equal(run("$('teaching-single').open"),true);
});

test('first teaching request is counted once across routes and extra editions have their own list',()=>{
    const {run,set}=dashboard();
    set(`state.papers=state.papers.slice(0,2);
        for(const p of state.papers){p.artifacts={requested:false};p.files=[{kind:'pdf',name:'paper.pdf'},{kind:'json',name:'paper.json',sha:'j'}]}
        state.papers[1].artifacts.requested=true;
        state.teaching_jobs=[{pmid:'2',route:'existing',version:'v',source_sha:'j'}];`);
    assert.deepEqual(run("scopedTeachingRows('existing',false).map(p=>p.pmid)"),['1']);
    assert.deepEqual(run("scopedTeachingRows('master',false).map(p=>p.pmid)"),['1']);
    assert.deepEqual(run("scopedTeachingRows('master',true).map(p=>p.pmid)"),['2']);
    set("state.papers[0].artifacts.requested=true;state.teaching_jobs.push({pmid:'1'})");
    assert.deepEqual(run("scopedTeachingRows('existing',false)"),[]);
});
test('teaching candidates show searchable publication types and keep selections when metadata changes',async()=>{
    const {set,run}=dashboard();
    set(`
        class Element {
            constructor(){this.children=[];this.value='';this.dataset={}}
            append(node){node.parent=this;this.children.push(node)}
            replaceChildren(){this.children=[]}
            closest(){return this.parent.parent}
            set id(key){elements.set(key,this);this.key=key}
            get id(){return this.key}
        }
        const elements=new Map();
        document.createElement=()=>new Element();
        document.getElementById=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
        var paper=state.papers[0];paper.publication_type='レビュー';paper.publication_type_inferred=true;
        $('teaching-batch-route').value='master';$('teaching-find').value='レビュー';
        renderTeachingOptions();
        var typeCell=()=>$('teaching-rows').children[0].children[3];
        var payload;act=(action,data)=>{payload={action,data}};
        $('teach-1').checked=true;$('teach-1').onchange();
    `);
    assert.equal(run('typeCell().children[0].textContent'),'レビュー');
    assert.equal(run('typeCell().children[1].textContent'),'タイトルから推定');
    await set("$('teaching-metadata').onclick()");
    assert.deepEqual(run('payload'),{action:'teaching-metadata',data:{pmids:['1']}});
    set("paper.publication_type_inferred=false;paper.publication_types=['Review'];$('teaching-find').value='review';renderTeachingOptions()");
    assert.equal(run('typeCell().children[1].textContent'),'PubMed · Review');
    assert.equal(run("$('teach-1').checked"),true);
});
