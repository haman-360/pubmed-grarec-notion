for (const choice of document.querySelectorAll('button[data-view]')) {
    choice.onclick = () => {
        for (const button of document.querySelectorAll('button[data-view]')) {
            document.getElementById(button.dataset.view).hidden = button !== choice;
            button.setAttribute('aria-pressed', String(button === choice));
        }
    };
}
