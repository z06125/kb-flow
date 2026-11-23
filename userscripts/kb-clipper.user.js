// ==UserScript==
// @name        KB 剪藏助手 (kb-clipper)
// @namespace   kb.clipper
// @version     1.0.0
// @description 任意网页一键剪藏进个人知识库：选中文字后 Ctrl+Shift+K，无选中则剪藏整页正文。服务器自动生成知识卡（一句话结论+要点+金句+标签）并入库供全文/语义检索。
// @match       *://*/*
// @grant       GM_xmlhttpRequest
// @grant       GM_getValue
// @grant       GM_setValue
// @grant       GM_registerMenuCommand
// @connect     47.93.243.93
// @connect     localhost
// @connect     127.0.0.1
// @run-at      document-idle
// ==/UserScript==

(function () {
  'use strict';

  const DEFAULT = { server: 'http://47.93.243.93:8000', token: '' };
  let cfg = Object.assign({}, DEFAULT, GM_getValue('cfg', {}) || {});

  const saveCfg = () => GM_setValue('cfg', cfg);

  function askConfig(force) {
    if (force || !cfg.token) {
      const s = prompt('KB 服务器地址', cfg.server);
      if (s == null) return false;
      const t = prompt('访问令牌 KB_TOKEN', cfg.token);
      if (t == null) return false;
      cfg = { server: s.trim().replace(/\/+$/, ''), token: t.trim() };
      saveCfg();
    }
    return true;
  }

  function toast(msg, ok = true) {
    const d = document.createElement('div');
    d.textContent = msg;
    d.style.cssText =
      'position:fixed;top:20px;right:20px;z-index:2147483647;' +
      `background:${ok ? '#123c1f' : '#3c1212'};color:#fff;padding:10px 16px;` +
      'border-radius:10px;font:14px/1.4 sans-serif;max-width:340px;' +
      'box-shadow:0 4px 16px rgba(0,0,0,.4);transition:opacity .4s';
    document.documentElement.appendChild(d);
    setTimeout(() => { d.style.opacity = '0'; setTimeout(() => d.remove(), 500); }, 3200);
  }

  function extractText() {
    const sel = window.getSelection ? String(window.getSelection()) : '';
    const base = sel.trim().length >= 20 ? sel : (document.body ? document.body.innerText : '');
    return base.replace(/\n{3,}/g, '\n\n').trim().slice(0, 100000);
  }

  function clip() {
    if (!askConfig(false)) return;
    const text = extractText();
    if (text.length < 20) { toast('内容太短（<20 字）：先选中一段文字，或换文字更多的页面', false); return; }
    toast('剪藏中，服务器正在生成知识卡…');
    GM_xmlhttpRequest({
      method: 'POST',
      url: cfg.server + '/v1/clip',
      headers: { 'X-API-Token': cfg.token, 'Content-Type': 'application/json' },
      data: JSON.stringify({
        url: location.href.split('#')[0],
        title: (document.title || '').trim(),
        text: text,
        source: 'clip',
        sync: true,
      }),
      timeout: 90000,
      onload(r) {
        let d = null;
        try { d = JSON.parse(r.responseText); } catch (e) { /* ignore */ }
        if (r.status === 200 && d) {
          toast(d.dup ? `已在库中（重复）#${d.id}` : `✅ 已入库 #${d.id}，知识卡已生成`);
        } else if (r.status === 401) {
          toast('令牌错误，请重新设置', false);
          askConfig(true);
        } else {
          toast(`失败 ${r.status}: ${(r.responseText || '').slice(0, 120)}`, false);
        }
      },
      onerror() { toast('网络错误：检查服务器地址与防火墙 8000 端口', false); },
      ontimeout() { toast('超时：正文过长或服务器繁忙', false); },
    });
  }

  GM_registerMenuCommand('⚙️ 设置服务器/令牌', () => askConfig(true));
  GM_registerMenuCommand('📌 剪藏（选中文字或整页）', clip);

  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey && (e.key === 'K' || e.key === 'k' || e.code === 'KeyK')) {
      e.preventDefault();
      clip();
    }
  }, true);
})();
