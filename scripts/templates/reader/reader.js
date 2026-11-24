const query = document.getElementById('query');
const sections = Array.from(document.querySelectorAll('main section'));
const links = Array.from(document.querySelectorAll('nav ol a'));
const summaries = sections.map(section => section.querySelector('details'));
const normalize = text => text.normalize('NFKC').toLocaleLowerCase();
const indexed = sections.map(section => normalize(section.textContent));
let prior = null;

function search() {
    const term = normalize(query.value.trim());
    if (term && !prior) prior = summaries.map(detail => detail.open);
    let count = 0;
    sections.forEach((section, i) => {
        const match = !term || indexed[i].includes(term);
        section.hidden = !match;
        links[i].parentElement.hidden = !match;
        if (term) summaries[i].open = match;
        else if (prior) summaries[i].open = prior[i];
        if (match) count++;
    });
    if (!term) prior = null;
    document.getElementById('matches').textContent = term ? count + ' セクションが一致' : '';
}
query.addEventListener('input', search);
links.forEach((link, i) => link.addEventListener('click', () => { summaries[i].open = true; }));
function openAnchor() {
    const target = document.getElementById(location.hash.slice(1));
    if (target && sections.includes(target)) target.querySelector('details').open = true;
}
window.addEventListener('hashchange', openAnchor);
openAnchor();
let printState;
window.addEventListener('beforeprint', () => {
    printState = summaries.map(detail => detail.open);
    summaries.forEach(detail => { detail.open = true; });
});
window.addEventListener('afterprint', () => {
    if (printState) summaries.forEach((detail, i) => { detail.open = printState[i]; });
    printState = null;
});
document.getElementById('tools').hidden = false;
