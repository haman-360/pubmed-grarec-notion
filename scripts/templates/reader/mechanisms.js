for (const group of document.querySelectorAll('.group')) {
    const choices = [...group.querySelectorAll('[data-select]')];
    const scenes = [...group.querySelectorAll('[data-scene]')];
    const previous = group.querySelector('[data-move="-1"]');
    const next = group.querySelector('[data-move="1"]');
    let selected = 0;
    function select(index) {
        selected = index;
        for (const [i, choice] of choices.entries()) choice.setAttribute('aria-pressed', String(i === index));
        for (const [i, scene] of scenes.entries()) scene.hidden = i !== index;
        const atBoundary = (index === 0 && document.activeElement === previous) ||
                           (index === choices.length - 1 && document.activeElement === next);
        previous.disabled = index === 0;
        next.disabled = index === choices.length - 1;
        group.querySelector('output').textContent = index ? choices[index].textContent.trim()+'　'+index+' / '+(choices.length-1) : '全体図';
        if (atBoundary) {
            choices[index].focus({preventScroll: true});
        }
    }
    for (const choice of choices) choice.onclick = () => select(Number(choice.dataset.select));
    previous.onclick = () => select(selected - 1);
    next.onclick = () => select(selected + 1);
}

const links = document.querySelectorAll('[data-pdf-page]');
if (links.length) {
    const bytes = Uint8Array.from(atob(document.getElementById('pdf').content.textContent), c => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], {type: 'application/pdf'}));
    for (const link of links) link.href = url + '#page=' + link.dataset.pdfPage;
}
