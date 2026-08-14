(() => {
    if (window.__eafEbookSelectionContextInstalled) return;
    window.__eafEbookSelectionContextInstalled = true;

    const normalize = text => (text || '').replace(/\s+/g, ' ').trim();
    const blockTags = new Set([
        'ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'DD', 'DIV', 'DL', 'DT',
        'FIGCAPTION', 'FIGURE', 'FOOTER', 'H1', 'H2', 'H3', 'H4', 'H5',
        'H6', 'HEADER', 'LI', 'MAIN', 'P', 'PRE', 'SECTION', 'TD', 'TH'
    ]);

    const contextForRange = (range, selectedText) => {
        let block = range.startContainer;
        while (block && block !== document.body) {
            const blockText = normalize(block.textContent);
            if (blockTags.has(block.nodeName) && blockText !== selectedText) break;
            block = block.parentNode;
        }
        if (!block) return '';

        const text = normalize(block.textContent);
        const prefix = document.createRange();
        prefix.selectNodeContents(block);
        prefix.setEnd(range.startContainer, range.startOffset);
        const offset = normalize(prefix.toString()).length;

        if (window.Intl && Intl.Segmenter) {
            const language = document.documentElement.lang || undefined;
            for (const part of new Intl.Segmenter(
                language, {granularity: 'sentence'}).segment(text)) {
                if (offset >= part.index && offset <= part.index + part.segment.length) {
                    return normalize(part.segment);
                }
            }
        }

        const selectedLength = selectedText.length;
        const markers = ['.', '!', '?', '\u3002', '\uff01', '\uff1f'];
        let start = 0;
        let end = text.length;
        for (const marker of markers) {
            const before = text.lastIndexOf(marker, offset - 1);
            const after = text.indexOf(marker, offset + selectedLength);
            if (before >= 0) start = Math.max(start, before + 1);
            if (after >= 0) end = Math.min(end, after + 1);
        }
        return normalize(text.slice(start, end));
    };

    const nodePath = node => {
        const path = [];
        while (node && node !== document) {
            const parent = node.parentNode;
            if (!parent) break;
            path.push(Array.prototype.indexOf.call(parent.childNodes, node));
            node = parent;
        }
        return path.reverse();
    };

    const rangeSnapshot = (range, source, word, context, point) => {
        const rect = range.getBoundingClientRect();
        return {
            source,
            word,
            selection: source === 'selection' ? word : '',
            context,
            document: {
                url: window.location.href,
                title: document.title || ''
            },
            range: {
                start_path: nodePath(range.startContainer),
                start_offset: range.startOffset,
                end_path: nodePath(range.endContainer),
                end_offset: range.endOffset
            },
            rect: {
                x: rect.x,
                y: rect.y,
                width: rect.width,
                height: rect.height
            },
            point: point || null
        };
    };

    const wordAtPoint = (x, y) => {
        const caret = document.caretRangeFromPoint(x, y);
        if (!caret || caret.startContainer.nodeType !== Node.TEXT_NODE) return null;
        const node = caret.startContainer;
        const text = node.data;
        const offset = Math.min(caret.startOffset, Math.max(0, text.length - 1));
        let start = offset;
        let end = offset;

        if (window.Intl && Intl.Segmenter) {
            const language = document.documentElement.lang || undefined;
            for (const part of new Intl.Segmenter(
                language, {granularity: 'word'}).segment(text)) {
                if (part.isWordLike && offset >= part.index &&
                    offset <= part.index + part.segment.length) {
                    start = part.index;
                    end = part.index + part.segment.length;
                    break;
                }
            }
        }
        if (start === end) {
            const isWord = char => char && /[\p{L}\p{N}'’-]/u.test(char);
            while (start > 0 && isWord(text[start - 1])) start--;
            while (end < text.length && isWord(text[end])) end++;
        }
        const word = normalize(text.slice(start, end));
        if (!word) return null;
        const range = document.createRange();
        range.setStart(node, start);
        range.setEnd(node, end);
        const context = contextForRange(range, word);
        return rangeSnapshot(range, 'mouse', word, context, {x, y});
    };

    const sendSelection = selection => {
        const text = normalize(selection.toString());
        if (!text) return;
        const range = selection.getRangeAt(0);
        window.top.postMessage(Object.assign(
            {type: 'eaf-ebook-text-context'},
            rangeSnapshot(range, 'selection', text,
                contextForRange(range, text), null)
        ), '*');
    };

    window.addEventListener('message', event => {
        const data = event.data;
        if (!data || data.type !== 'eaf-ebook-text-context-at-point') return;
        const selection = window.getSelection();
        if (selection && !selection.isCollapsed && selection.rangeCount) {
            sendSelection(selection);
            return;
        }
        const hover = wordAtPoint(data.x, data.y);
        window.top.postMessage(Object.assign(
            {type: 'eaf-ebook-text-context'},
            hover || {source: 'mouse', word: '', selection: '', context: '',
                document: {url: window.location.href, title: document.title || ''},
                range: null, rect: null, point: {x: data.x, y: data.y}}
        ), '*');
    });

    if (window !== window.top) return;

    window.eafEbookTextContextAtPoint = (x, y) => {
        const target = document.elementFromPoint(x, y);
        const frame = target && target.tagName === 'IFRAME'
            ? target
            : document.querySelector('iframe');
        if (!frame || !frame.contentWindow) {
            window.postMessage({type: 'eaf-ebook-text-context-at-point', x, y}, '*');
            return;
        }
        const rect = frame.getBoundingClientRect();
        frame.contentWindow.postMessage({
            type: 'eaf-ebook-text-context-at-point',
            x: x - rect.left,
            y: y - rect.top
        }, '*');
    };

    const connect = () => {
        if (!window.qt || !qt.webChannelTransport || !window.QWebChannel) {
            window.setTimeout(connect, 50);
            return;
        }
        new QWebChannel(qt.webChannelTransport, channel => {
            const pyobject = channel.objects.pyobject;
            window.addEventListener('message', event => {
                const data = event.data;
                if (!data) return;
                if (data.type === 'eaf-ebook-text-context') {
                    pyobject.text_context_changed(JSON.stringify(data));
                }
            });
        });
    };
    connect();
})();
