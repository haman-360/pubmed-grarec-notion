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
test('manual links keep full destinations but use short labels and reject executable URLs',()=>{
    const {run,set}=dashboard();set("var links=[];var parent={append:a=>links.push(a)};manualLink(parent,'https://doi.org/10.1000/test');manualLink(parent,'https://example.org/'+ 'long-title-'.repeat(50));manualLink(parent,'javascript:alert(1)');manualLink(parent,'not a URL')");
    const links=run('links');assert.equal(links.length,2);assert.equal(links[0].textContent,'出版社（DOI）');assert.equal(links[0].href,'https://doi.org/10.1000/test');assert.equal(links[1].textContent,'example.org');assert.ok(links[1].href.length>500);assert.equal(links[1].rel,'noopener noreferrer');
});
