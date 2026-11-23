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
test('a held PDF offers a reading guide and a separate one-paper estimate without enabling send',async()=>{
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
        openReadingGuide=p=>calls.push(p.pmid);
        act=(action,payload)=>calls.push({action,payload});
        state={busy:false,papers:[{pmid:'1',heads:['a'],title:'Review',publication_type:'レビュー',category:'json_pending',files:[]}],
            json_batch:{is_owner:true,key_ready:true,jobs:[],preview:{id:'hold',policy:1,items:[],total:0,
                held:[{pmid:'1',heads:['a'],title:'Review',publication_type:'レビュー',reason:'長文',can_estimate_full:true}]}}};
        renderJsonPanel();
        var actions=()=> $('json-held').children[1].children[2].children;
    `);
    assert.equal(run("$('json-send').disabled"),true);
    assert.equal(run("$('json-preview').hidden"),true);
    assert.equal(run("$('json-held').hidden"),false);
    set('actions()[0].onclick();actions()[1].onclick()');
    assert.deepEqual(run('calls'),['1',{action:'json-prepare',payload:{items:[{pmid:'1',heads:['a']}],include_long:true}}]);
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
