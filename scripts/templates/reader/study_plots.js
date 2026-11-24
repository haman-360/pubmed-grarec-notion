const pdf = document.getElementById('pdf');
let pdfUrl;
document.addEventListener('click', event => {
    const point = event.target.closest('button.point');
    if (point) {
        const dialog = point.nextElementSibling;
        const links = dialog.querySelectorAll('[data-pdf-page]');
        if (links.length && !pdfUrl) {
            const bytes = Uint8Array.from(atob(pdf.content.textContent), char => char.charCodeAt(0));
            pdfUrl = URL.createObjectURL(new Blob([bytes], {type: 'application/pdf'}));
        }
        for (const link of links) link.href = pdfUrl + '#page=' + link.dataset.pdfPage;
        point.focus({preventScroll: true});
        dialog.showModal();
        dialog.scrollTop = 0;
    }
    const close = event.target.closest('button.close');
    if (close) close.closest('dialog').close();
});
