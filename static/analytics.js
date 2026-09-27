'use strict';

(() => {
  const signal = navigator.doNotTrack || navigator.msDoNotTrack || window.doNotTrack;
  const doNotTrack = signal === true || /^(1|yes)$/i.test(String(signal || ''));
  const trackingAllowed = !doNotTrack && navigator.globalPrivacyControl !== true;
  const websiteId = 'd3f0e8b5-20e0-4649-8bad-2ba3c26eabe6';
  const titles = Object.freeze({
    '/': 'WhichModel', '/playground': 'WhichModel', '/cache': 'Cache explorer',
    '/results': 'Results', '/how': 'How it works', '/run': 'Setup guide',
    '/privacy': 'Privacy', '/impressum': 'Legal notice', '/evidence': 'Evidence',
    '/claims': 'Evidence',
  });

  if (trackingAllowed) {
    window.whichModelUmamiBeforeSend = (type, payload) => {
      if (type !== 'event' || !payload || typeof payload !== 'object' ||
          Object.prototype.hasOwnProperty.call(payload, 'name') ||
          Object.prototype.hasOwnProperty.call(payload, 'data')) return false;
      let page;
      try { page = new URL(payload.url, window.location.origin); }
      catch { return false; }
      const title = titles[page.pathname];
      if (page.origin !== window.location.origin || !title) return false;
      return {
        website: websiteId,
        hostname: 'whichmodel.app.mintapis.com',
        url: page.pathname,
        title,
        referrer: '',
      };
    };

    const script = document.createElement('script');
    script.defer = true;
    script.src = 'https://bh-analytics.app.mintapis.com/script.js';
    script.integrity = 'sha384-ZMxgpYfO/phGz4GiYTIZhcauuGKTb2onmOB5gsiigjmBR38DGAmIna5J1Y/dM/13';
    script.crossOrigin = 'anonymous';
    script.referrerPolicy = 'no-referrer';
    script.dataset.websiteId = websiteId;
    script.dataset.domains = 'whichmodel.app.mintapis.com';
    script.dataset.doNotTrack = 'true';
    script.dataset.excludeHash = 'true';
    script.dataset.beforeSend = 'whichModelUmamiBeforeSend';
    document.head.append(script);
  }

  const counter = document.querySelector('#visitor-count');
  if (counter) {
    fetch('/api/analytics/visits', {
      credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer',
      headers: { Accept: 'application/json' },
    }).then(response => response.ok ? response.json() : null)
      .then(data => {
        if (!data || !Number.isSafeInteger(data.visits) || data.visits < 0) return;
        counter.textContent = `${data.visits} ${data.visits === 1 ? 'visit' : 'visits'}`;
        counter.hidden = false;
      }).catch(() => {});
  }
})();
