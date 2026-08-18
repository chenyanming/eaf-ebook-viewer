(() => {
    if (window.__eafEbookTextHighlightsInstalled) return;
    window.__eafEbookTextHighlightsInstalled = true;

    const overlayId = 'eaf-ebook-text-highlight-overlay';
    const messageSet = 'eaf-ebook-set-text-highlights';
    const messageReady = 'eaf-ebook-text-highlights-ready';
    let generation = 0;
    let words = [];
    let ranges = [];
    let positionFrame = null;
    let viewportTimer = null;

    const report = status => {
        window.top.postMessage(Object.assign({
            type: 'eaf-ebook-text-highlights-status',
            document: window.location.href
        }, status), '*');
    };

    const normalize = value => String(value || '').trim().toLocaleLowerCase();
    const schedule = callback => {
        if (window.requestIdleCallback) {
            window.requestIdleCallback(callback, {timeout: 100});
        } else {
            window.setTimeout(() => callback({timeRemaining: () => 8}), 0);
        }
    };

    const ensureOverlay = () => {
        let overlay = document.getElementById(overlayId);
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.id = overlayId;
        Object.assign(overlay.style, {
            position: 'fixed',
            inset: '0',
            width: '100vw',
            height: '100vh',
            overflow: 'hidden',
            pointerEvents: 'none',
            zIndex: '2147483646'
        });
        document.documentElement.appendChild(overlay);
        return overlay;
    };

    const clearOverlay = () => {
        document.getElementById(overlayId)?.replaceChildren();
    };

    const textWalker = () => document.createTreeWalker(
            document.body || document.documentElement,
            NodeFilter.SHOW_TEXT,
            {
                acceptNode(node) {
                    if (!node.data || !node.data.trim()) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    const parent = node.parentElement;
                    if (!parent || parent.closest(
                        `#${overlayId}, script, style, noscript, textarea, input, ` +
                        '[contenteditable="true"]'
                    )) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    return NodeFilter.FILTER_ACCEPT;
                }
            }
        );

    const isVisible = node => {
        const width = window.innerWidth;
        const height = window.innerHeight;
        const range = document.createRange();
        range.selectNodeContents(node);
        return Array.from(range.getClientRects()).some(
            rect => rect.bottom >= 0 && rect.top <= height &&
                rect.right >= 0 && rect.left <= width
        );
    };

    const rangesForNode = (node, wanted, segmenter) => {
        const found = [];
        const text = node.data;
        if (segmenter) {
            for (const part of segmenter.segment(text)) {
                const style = wanted.get(normalize(part.segment));
                if (!part.isWordLike || style === undefined) continue;
                const range = document.createRange();
                range.setStart(node, part.index);
                range.setEnd(node, part.index + part.segment.length);
                found.push({range, style});
            }
            return found;
        }
        const pattern = /[\p{L}\p{N}'’-]+/gu;
        for (const match of text.matchAll(pattern)) {
            const style = wanted.get(normalize(match[0]));
            if (style === undefined) continue;
            const range = document.createRange();
            range.setStart(node, match.index);
            range.setEnd(node, match.index + match[0].length);
            found.push({range, style});
        }
        return found;
    };

    const positionHighlights = () => {
        positionFrame = null;
        const overlay = ensureOverlay();
        const fragment = document.createDocumentFragment();
        const width = window.innerWidth;
        const height = window.innerHeight;
        let visible = 0;
        for (const match of ranges) {
            const {range, style = {}} = match;
            if (!range.startContainer?.isConnected) continue;
            for (const rect of range.getClientRects()) {
                if (rect.bottom < 0 || rect.top > height ||
                    rect.right < 0 || rect.left > width) continue;
                const marker = document.createElement('span');
                Object.assign(marker.style, {
                    position: 'absolute',
                    left: `${rect.left}px`,
                    top: `${rect.top}px`,
                    width: `${rect.width}px`,
                    height: `${rect.height}px`,
                    boxSizing: 'border-box',
                    borderBottom: `2px solid ${style.border || 'rgba(214, 146, 0, 0.95)'}`,
                    borderRadius: '2px',
                    background: style.background || 'rgba(255, 216, 96, 0.28)',
                    opacity: Number.isFinite(Number(style.opacity))
                        ? String(style.opacity) : '1',
                    pointerEvents: 'none'
                });
                fragment.appendChild(marker);
                visible++;
            }
        }
        overlay.replaceChildren(fragment);
        return visible;
    };

    const schedulePosition = () => {
        if (positionFrame !== null) return;
        positionFrame = window.requestAnimationFrame(positionHighlights);
    };

    const scheduleViewportRender = () => {
        schedulePosition();
        window.clearTimeout(viewportTimer);
        viewportTimer = window.setTimeout(() => render(words), 120);
    };

    const render = entries => {
        words = Array.isArray(entries) ? entries : [];
        const token = ++generation;
        const wanted = new Map();
        for (const entry of words) {
            const word = normalize(
                typeof entry === 'string' ? entry : entry && entry.word
            );
            if (!word) continue;
            wanted.set(word,
                typeof entry === 'object' && entry && entry.style
                    ? entry.style : {});
        }
        if (!wanted.size) {
            ranges = [];
            clearOverlay();
            report({supported: true, words: 0, ranges: 0, visible: 0});
            return;
        }

        const walker = textWalker();
        let segmenter = null;
        if (window.Intl && Intl.Segmenter) {
            try {
                segmenter = new Intl.Segmenter(
                    document.documentElement.lang || undefined,
                    {granularity: 'word'}
                );
            } catch (_) {
                segmenter = new Intl.Segmenter(undefined, {granularity: 'word'});
            }
        }
        const found = [];
        let scanned = 0;
        const scan = deadline => {
            if (token !== generation) return;
            let count = 0;
            let node = null;
            while ((node = walker.nextNode())) {
                if (isVisible(node)) {
                    found.push(...rangesForNode(node, wanted, segmenter));
                }
                scanned++;
                count++;
                if (count >= 100 && deadline.timeRemaining() <= 1) {
                    schedule(scan);
                    return;
                }
            }
            ranges = found;
            const visible = positionHighlights();
            report({
                supported: true,
                words: wanted.size,
                ranges: ranges.length,
                visible,
                nodes: scanned
            });
        };
        schedule(scan);
    };

    window.addEventListener('message', event => {
        const data = event.data;
        if (!data) return;
        if (data.type === messageSet && window !== window.top) {
            render(data.words);
        } else if (data.type === messageReady && window === window.top) {
            event.source.postMessage({type: messageSet, words}, '*');
        }
    });

    if (window !== window.top) {
        let renderTimer = null;
        new MutationObserver(() => {
            generation++;
            ranges = [];
            clearOverlay();
            window.clearTimeout(renderTimer);
            renderTimer = window.setTimeout(() => render(words), 120);
        }).observe(
            document.body || document.documentElement,
            {childList: true, subtree: true, characterData: true}
        );
        window.addEventListener('scroll', scheduleViewportRender, true);
        window.addEventListener('resize', scheduleViewportRender);
        window.visualViewport?.addEventListener('scroll', scheduleViewportRender);
        window.visualViewport?.addEventListener('resize', scheduleViewportRender);
        window.top.postMessage({type: messageReady}, '*');
        return;
    }

    const sendToFrames = () => {
        for (const frame of document.querySelectorAll('iframe')) {
            frame.contentWindow?.postMessage({type: messageSet, words}, '*');
        }
    };

    window.eafEbookSetTextHighlights = entries => {
        words = Array.isArray(entries) ? entries : [];
        sendToFrames();
    };
})();
