const lessons = [...document.querySelectorAll('main section')];
const el = id => document.getElementById(id);
const audit = el('audit'), input = el('find'), slides = el('slides');
// Each study card or diagram can be read on its own slide. Continuous view uses the same DOM.
const frames = lessons.flatMap((section, index) => {
    const cards = [...section.querySelectorAll(':scope > article, :scope > .diagram')];
    return [{index, card:null, cards}, ...cards.map(card => ({index, card, cards}))];
});
let slide = false, frameIndex = 0;
const norm = value => value.normalize('NFKC').toLocaleLowerCase();
function show() {
    const query = norm(input.value.trim());
    const frame = frames[frameIndex];
    let count = 0;
    lessons.forEach((node, i) => {
        const match = norm(node.textContent).includes(query);
        count += Number(match);
        node.hidden = slide ? i !== frame.index : !match;
        for (const child of node.children) {
            if (child.matches('h2, small')) continue;
            child.hidden = slide && i === frame.index && (frame.card ? child !== frame.card : frame.cards.includes(child));
        }
    });
    audit.hidden = slide;
    el('pager').hidden = !slide;
    slides.setAttribute('aria-pressed', String(slide));
    slides.textContent = slide ? '通常表示' : 'スライド表示';
    el('pos').value = (frameIndex + 1) + ' / ' + frames.length;
    el('prev').disabled = frameIndex === 0;
    el('next').disabled = frameIndex === frames.length - 1;
    el('matches').value = query ? count + '単元が一致' : '';
}
function jump() {
    const node = document.getElementById(location.hash.slice(1));
    if (!node) return;
    if (lessons.includes(node)) frameIndex = frames.findIndex(frame => lessons[frame.index] === node);
    if (audit.contains(node)) {
        slide = false;
        audit.open = true;
        if (node.tagName === 'DETAILS') node.open = true;
    }
    input.value = '';
    show();
    node.scrollIntoView();
}
function move(delta) {
    frameIndex = Math.min(frames.length-1,Math.max(0,frameIndex+delta));
    show();
    el('pager').scrollIntoView();
}
slides.onclick = () => { slide = !slide; input.value = ''; show(); };
el('prev').onclick = () => move(-1);
el('next').onclick = () => move(1);
input.oninput = () => { slide = false; show(); };
document.addEventListener('keydown', event => {
    if (!slide || event.target === input) return;
    if (['ArrowLeft','ArrowRight'].includes(event.key)) {
        event.preventDefault();
        move(event.key === 'ArrowLeft' ? -1 : 1);
    }
});
document.querySelectorAll('a[href^="#"]').forEach(a => a.addEventListener('click', () => {
    if (a.hash === location.hash) jump();
}));
window.addEventListener('hashchange',jump);
el('tools').hidden = false;
show();
jump();

const pdf = el('pdf').content.textContent.trim();
if (pdf) {
    const bytes = Uint8Array.from(atob(pdf), c => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], {type:'application/pdf'}));
    document.querySelectorAll('[data-pdf-page]').forEach(link => {
        link.href = url + '#page=' + link.dataset.pdfPage;
    });
}
