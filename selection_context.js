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
        // caretRangeFromPoint() can snap an empty part of the page to the
        // nearest text node.  Only accept clicks that actually intersect the
        // rendered word.
        const hitsWord = Array.from(range.getClientRects()).some(rect =>
            x >= rect.left && x <= rect.right &&
            y >= rect.top && y <= rect.bottom
        );
        if (!hitsWord) return null;
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

    let selectionTimer = null;
    if (window !== window.top) {
        document.addEventListener('selectionchange', () => {
            window.clearTimeout(selectionTimer);
            selectionTimer = window.setTimeout(() => {
                const selection = window.getSelection();
                if (selection && !selection.isCollapsed && selection.rangeCount) {
                    sendSelection(selection);
                }
            }, 40);
        });

        document.addEventListener('click', event => {
            if (event.button !== 0) return;
            const selection = window.getSelection();
            if (selection && !selection.isCollapsed) return;
            const clicked = wordAtPoint(event.clientX, event.clientY);
            if (!clicked) return;
            window.top.postMessage(Object.assign(
                {
                    type: 'eaf-ebook-word-clicked',
                    link: event.target.closest?.('a[href]')?.href || ''
                },
                clicked,
                {source: 'click'}
            ), '*');
        });
    }

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

    if (window !== window.top) {
        let lastDocumentText = null;
        let documentTimer = null;
        let documentGeneration = 0;
        const scheduleWork = callback => {
            if (window.requestIdleCallback) {
                window.requestIdleCallback(callback, {timeout: 100});
            } else {
                window.setTimeout(
                    () => callback({timeRemaining: () => 8}), 0
                );
            }
        };
        const sendDocumentText = () => {
            documentTimer = null;
            const token = documentGeneration;
            const width = window.innerWidth;
            const height = window.innerHeight;
            const chunks = [];
            const walker = document.createTreeWalker(
                document.body || document.documentElement,
                NodeFilter.SHOW_TEXT,
                {
                    acceptNode(node) {
                        if (!node.data || !node.data.trim()) {
                            return NodeFilter.FILTER_REJECT;
                        }
                        const parent = node.parentElement;
                        if (!parent || parent.closest(
                            'script, style, noscript, textarea, input, ' +
                            '[contenteditable="true"], ' +
                            '#eaf-ebook-text-highlight-overlay'
                        )) {
                            return NodeFilter.FILTER_REJECT;
                        }
                        return NodeFilter.FILTER_ACCEPT;
                    }
                }
            );
            const scan = deadline => {
                if (token !== documentGeneration) return;
                let count = 0;
                let node = null;
                while ((node = walker.nextNode())) {
                    const range = document.createRange();
                    range.selectNodeContents(node);
                    const visible = Array.from(range.getClientRects()).some(
                        rect => rect.bottom >= 0 && rect.top <= height &&
                            rect.right >= 0 && rect.left <= width
                    );
                    if (visible) chunks.push(node.data);
                    count++;
                    if (count >= 100 && deadline.timeRemaining() <= 1) {
                        scheduleWork(scan);
                        return;
                    }
                }
                const text = normalize(chunks.join(' '));
                if (text === lastDocumentText) return;
                lastDocumentText = text;
                window.top.postMessage({
                    type: 'eaf-ebook-document-changed',
                    text,
                    scope: 'page',
                    language: document.documentElement.lang || '',
                    document: {
                        url: window.location.href,
                        title: document.title || ''
                    }
                }, '*');
            };
            scheduleWork(scan);
        };
        const scheduleDocumentText = () => {
            documentGeneration++;
            window.clearTimeout(documentTimer);
            documentTimer = window.setTimeout(sendDocumentText, 120);
        };
        new MutationObserver(scheduleDocumentText).observe(
            document.body || document.documentElement,
            {childList: true, subtree: true, characterData: true}
        );
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', scheduleDocumentText,
                {once: true});
        } else {
            scheduleDocumentText();
        }
        window.addEventListener('scroll', scheduleDocumentText, true);
        window.addEventListener('resize', scheduleDocumentText);
        window.visualViewport?.addEventListener('scroll', scheduleDocumentText);
        window.visualViewport?.addEventListener('resize', scheduleDocumentText);
        return;
    }

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

    window.eafEbookCreateHighlight = () => {
        const selectionBar =
            document.getElementById('book-selection-bar-overlay');
        if (!selectionBar ||
            window.getComputedStyle(selectionBar).display === 'none') {
            return false;
        }
        selectionBar.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'q',
            bubbles: true,
            cancelable: true
        }));
        return true;
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
                } else if (data.type === 'eaf-ebook-word-clicked') {
                    pyobject.word_clicked(JSON.stringify(data));
                } else if (data.type === 'eaf-ebook-document-changed') {
                    pyobject.document_text_changed(JSON.stringify(data));
                } else if (data.type === 'eaf-ebook-text-highlights-status') {
                    pyobject.text_highlights_rendered(JSON.stringify(data));
                }
            });
        });
    };
    connect();
})();
