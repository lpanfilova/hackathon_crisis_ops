"""Dependency-free CrisisOps web dashboard.

Run with::

    python3 frontend/app.py

Then open http://127.0.0.1:8000. The repository currently has no implemented
backend routes, parser, allocator, or data schema, so this frontend uses a
clearly-labelled local demonstration scenario. UI state stays in the browser
and the integration boundary is intentionally isolated in ``window.CrisisOps``.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

HOST: Final = "127.0.0.1"
PORT: Final = 8000

PAGE: Final = r'''<!doctype html>
<html lang="en" data-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="CrisisOps disaster food distribution command center">
  <meta name="theme-color" content="#0f766e">
  <title>CrisisOps · Food Distribution Command Center</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f7f8;
      --bg-accent: #eaf4f3;
      --surface: #ffffff;
      --surface-2: #f8fafb;
      --surface-3: #eef3f4;
      --text: #132326;
      --text-2: #53666a;
      --text-3: #718387;
      --border: #dbe5e7;
      --border-strong: #c7d5d8;
      --brand: #0f766e;
      --brand-strong: #0a5d57;
      --brand-soft: #d9f1ee;
      --accent: #0e7490;
      --warning: #b45309;
      --warning-soft: #fff2d8;
      --danger: #be3b43;
      --danger-soft: #fee9ea;
      --success: #177245;
      --success-soft: #def4e8;
      --info-soft: #e0f2fe;
      --shadow-sm: 0 1px 2px rgba(15, 35, 38, .05), 0 3px 10px rgba(15, 35, 38, .04);
      --shadow-md: 0 12px 30px rgba(15, 35, 38, .09), 0 2px 8px rgba(15, 35, 38, .04);
      --shadow-lg: 0 24px 70px rgba(8, 28, 31, .22);
      --radius-sm: 10px;
      --radius: 16px;
      --radius-lg: 22px;
      --header-height: 78px;
      --focus: 0 0 0 3px rgba(15, 118, 110, .22);
    }

    html[data-theme="dark"] {
      color-scheme: dark;
      --bg: #091315;
      --bg-accent: #0b1d1e;
      --surface: #111d20;
      --surface-2: #152428;
      --surface-3: #1b2c30;
      --text: #e9f2f3;
      --text-2: #a9bec2;
      --text-3: #82999d;
      --border: #273a3e;
      --border-strong: #354b50;
      --brand: #39b9ad;
      --brand-strong: #67d4ca;
      --brand-soft: #153c3b;
      --accent: #5bc0db;
      --warning: #f0a84d;
      --warning-soft: #3c2b17;
      --danger: #ef7e84;
      --danger-soft: #3e2226;
      --success: #62ca91;
      --success-soft: #19382a;
      --info-soft: #173443;
      --shadow-sm: 0 1px 2px rgba(0, 0, 0, .2), 0 3px 12px rgba(0, 0, 0, .12);
      --shadow-md: 0 14px 35px rgba(0, 0, 0, .24), 0 2px 8px rgba(0, 0, 0, .18);
      --shadow-lg: 0 28px 80px rgba(0, 0, 0, .48);
      --focus: 0 0 0 3px rgba(57, 185, 173, .28);
    }

    *, *::before, *::after { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      min-width: 320px;
      min-height: 100vh;
      overflow-x: hidden;
      background:
        radial-gradient(circle at 8% -8%, var(--bg-accent) 0, transparent 32rem),
        var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
      transition: background-color .2s ease, color .2s ease;
    }
    button, textarea { font: inherit; }
    button { color: inherit; }
    button:focus-visible, textarea:focus-visible { outline: none; box-shadow: var(--focus); }
    button, [role="button"] { -webkit-tap-highlight-color: transparent; }
    svg { display: block; }
    .sr-only {
      position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
      overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0;
    }

    .app-header {
      position: sticky;
      top: 0;
      z-index: 30;
      height: var(--header-height);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      padding: 0 32px;
      background: color-mix(in srgb, var(--surface) 90%, transparent);
      border-bottom: 1px solid var(--border);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
    }
    .brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
    .brand-mark {
      width: 42px; height: 42px; flex: 0 0 auto; border-radius: 13px;
      display: grid; place-items: center; color: white;
      background: linear-gradient(145deg, #11998e, #0f766e 58%, #07514d);
      box-shadow: 0 7px 18px rgba(15,118,110,.24);
    }
    .brand-mark svg { width: 23px; height: 23px; }
    .brand-copy { min-width: 0; }
    .brand-name { margin: 0; font-size: 18px; line-height: 1.1; letter-spacing: -.025em; font-weight: 760; }
    .brand-tagline { margin-top: 3px; color: var(--text-2); font-size: 12px; white-space: nowrap; }
    .header-actions { display: flex; align-items: center; gap: 10px; }
    .sync-block {
      display: flex; align-items: center; gap: 9px; min-width: 205px;
      padding: 8px 12px; border: 1px solid var(--border); border-radius: 12px;
      background: var(--surface-2);
    }
    .sync-dot { position: relative; width: 9px; height: 9px; border-radius: 50%; background: var(--success); flex: 0 0 auto; }
    .sync-dot::after {
      content: ""; position: absolute; inset: -4px; border: 1px solid var(--success);
      border-radius: inherit; opacity: .25; animation: pulse 2.4s infinite;
    }
    @keyframes pulse { 70%, 100% { transform: scale(1.7); opacity: 0; } }
    .sync-label { color: var(--text-3); font-size: 10px; letter-spacing: .07em; text-transform: uppercase; font-weight: 700; line-height: 1; }
    .sync-time { margin-top: 4px; color: var(--text); font-size: 12px; font-variant-numeric: tabular-nums; line-height: 1; }
    .icon-button {
      width: 40px; height: 40px; border: 1px solid var(--border); border-radius: 12px;
      display: grid; place-items: center; cursor: pointer; background: var(--surface-2);
      transition: transform .16s ease, border-color .16s ease, background .16s ease;
    }
    .icon-button:hover { transform: translateY(-1px); border-color: var(--border-strong); background: var(--surface-3); }
    .icon-button svg { width: 18px; height: 18px; }

    .main-shell { max-width: 1600px; margin: 0 auto; padding: 26px 30px 36px; }
    .command-bar {
      display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin-bottom: 18px;
    }
    .eyebrow { color: var(--brand); font-size: 11px; font-weight: 750; letter-spacing: .13em; text-transform: uppercase; }
    .page-title { margin: 4px 0 3px; font-size: clamp(22px, 3vw, 30px); letter-spacing: -.035em; line-height: 1.2; }
    .page-description { margin: 0; color: var(--text-2); font-size: 13px; }
    .status-pills { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
    .status-pill {
      display: inline-flex; align-items: center; gap: 7px; padding: 7px 10px;
      border: 1px solid var(--border); border-radius: 999px; background: var(--surface);
      color: var(--text-2); font-size: 11px; font-weight: 650;
    }
    .status-pill strong { color: var(--text); }
    .mini-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--brand); }

    .dashboard-grid {
      display: grid;
      grid-template-columns: minmax(285px, .96fr) minmax(310px, .98fr) minmax(350px, 1.14fr);
      gap: 16px;
      align-items: stretch;
    }
    .panel {
      min-width: 0;
      min-height: calc(100vh - 210px);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      background: var(--surface);
      box-shadow: var(--shadow-sm);
    }
    .panel-header {
      min-height: 73px; display: flex; align-items: center; justify-content: space-between; gap: 12px;
      padding: 16px 18px; border-bottom: 1px solid var(--border);
      background: linear-gradient(180deg, color-mix(in srgb, var(--surface-2) 65%, transparent), transparent);
    }
    .panel-heading { display: flex; align-items: center; gap: 11px; min-width: 0; }
    .panel-icon {
      width: 36px; height: 36px; flex: 0 0 auto; display: grid; place-items: center;
      border-radius: 11px; color: var(--brand); background: var(--brand-soft);
    }
    .panel-icon svg { width: 18px; height: 18px; }
    .panel-title { margin: 0; font-size: 15px; line-height: 1.15; letter-spacing: -.012em; }
    .panel-subtitle { margin: 4px 0 0; color: var(--text-2); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .expand-button {
      width: 35px; height: 35px; flex: 0 0 auto; border: 1px solid var(--border); border-radius: 10px;
      display: grid; place-items: center; cursor: pointer; background: transparent;
      transition: background .15s ease, border-color .15s ease;
    }
    .expand-button:hover { background: var(--surface-3); border-color: var(--border-strong); }
    .expand-button svg { width: 16px; height: 16px; }
    .panel-content { padding: 16px; display: flex; flex: 1; flex-direction: column; min-height: 0; }

    .metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 9px; margin-bottom: 14px; }
    .metric-card {
      min-width: 0; padding: 13px 12px; border: 1px solid var(--border); border-radius: 14px;
      background: var(--surface-2);
    }
    .metric-top { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
    .metric-label { color: var(--text-2); font-size: 10px; font-weight: 680; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .metric-icon { color: var(--text-3); }
    .metric-icon svg { width: 14px; height: 14px; }
    .metric-value { margin-top: 5px; font-size: 22px; font-weight: 760; line-height: 1; letter-spacing: -.04em; font-variant-numeric: tabular-nums; }
    .metric-unit { color: var(--text-3); font-size: 10px; font-weight: 550; margin-left: 2px; letter-spacing: 0; }
    .metric-delta { margin-top: 5px; color: var(--success); font-size: 9px; font-weight: 650; }

    .resource-section { border: 1px solid var(--border); border-radius: 15px; overflow: hidden; background: var(--surface-2); }
    .resource-section + .resource-section { margin-top: 12px; }
    .resource-section:last-child { flex: 1; }
    .section-head {
      padding: 11px 13px 9px; display: flex; align-items: center; justify-content: space-between; gap: 10px;
      border-bottom: 1px solid var(--border);
    }
    .section-title { margin: 0; font-size: 11px; letter-spacing: .045em; text-transform: uppercase; font-weight: 750; }
    .section-count { color: var(--text-3); font-size: 10px; }
    .resource-list { display: grid; }
    .resource-item { padding: 11px 13px; display: grid; grid-template-columns: minmax(0,1fr) auto; gap: 12px; align-items: center; }
    .resource-item + .resource-item { border-top: 1px solid var(--border); }
    .resource-name { font-size: 12px; font-weight: 680; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .resource-meta { margin-top: 3px; color: var(--text-3); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .inventory { text-align: right; min-width: 70px; }
    .inventory-value { font-size: 11px; font-weight: 700; font-variant-numeric: tabular-nums; }
    .progress { height: 4px; margin-top: 6px; border-radius: 999px; overflow: hidden; background: var(--border); }
    .progress > span { height: 100%; display: block; border-radius: inherit; background: var(--brand); }
    .progress.warning > span { background: var(--warning); }
    .tag {
      display: inline-flex; align-items: center; gap: 5px; padding: 4px 7px; border-radius: 999px;
      font-size: 9px; font-weight: 700; white-space: nowrap;
    }
    .tag.success { color: var(--success); background: var(--success-soft); }
    .tag.warning { color: var(--warning); background: var(--warning-soft); }
    .tag.danger { color: var(--danger); background: var(--danger-soft); }
    .tag.neutral { color: var(--text-2); background: var(--surface-3); }

    .incident-form { display: flex; flex-direction: column; flex: 1; min-height: 0; }
    .field-label-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
    .field-label { font-size: 11px; font-weight: 720; }
    .field-hint { color: var(--text-3); font-size: 9px; }
    .textarea-wrap { position: relative; }
    textarea {
      width: 100%; min-height: 122px; resize: vertical; display: block;
      padding: 13px 14px 42px; border: 1px solid var(--border-strong); border-radius: 14px;
      background: var(--surface-2); color: var(--text); line-height: 1.55;
      transition: border-color .15s ease, background .15s ease;
    }
    textarea:hover { border-color: color-mix(in srgb, var(--brand) 50%, var(--border)); }
    textarea::placeholder { color: var(--text-3); }
    .textarea-actions { position: absolute; left: 10px; right: 9px; bottom: 8px; display: flex; align-items: center; justify-content: space-between; }
    .sample-button { border: 0; background: transparent; color: var(--brand); font-size: 10px; font-weight: 700; cursor: pointer; padding: 5px; }
    .analyze-button {
      border: 0; border-radius: 9px; background: var(--brand); color: #fff; cursor: pointer;
      padding: 7px 11px; font-size: 10px; font-weight: 720; display: inline-flex; align-items: center; gap: 6px;
      transition: background .15s ease, transform .15s ease;
    }
    .analyze-button:hover { background: var(--brand-strong); transform: translateY(-1px); }
    .analyze-button svg { width: 12px; height: 12px; }

    .interpretation { margin-top: 13px; border: 1px solid var(--border); border-radius: 15px; overflow: hidden; background: var(--surface-2); }
    .interpretation-header { padding: 11px 13px; display: flex; align-items: center; gap: 9px; border-bottom: 1px solid var(--border); }
    .ai-mark {
      width: 27px; height: 27px; flex: 0 0 auto; display: grid; place-items: center;
      border-radius: 9px; color: var(--brand); background: var(--brand-soft);
    }
    .ai-mark svg { width: 14px; height: 14px; }
    .ai-title { font-size: 11px; font-weight: 730; }
    .ai-subtitle { margin-top: 2px; color: var(--text-3); font-size: 9px; }
    .confidence { margin-left: auto; color: var(--success); background: var(--success-soft); border-radius: 999px; padding: 4px 7px; font-size: 9px; font-weight: 720; }
    .interpretation-body { padding: 12px 13px; min-height: 112px; }
    .empty-ai { height: 87px; display: grid; place-items: center; text-align: center; color: var(--text-3); font-size: 10px; }
    .extraction-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; }
    .extraction-item span { display: block; color: var(--text-3); font-size: 9px; text-transform: uppercase; letter-spacing: .06em; font-weight: 700; }
    .extraction-item strong { display: block; margin-top: 3px; font-size: 11px; font-weight: 680; }
    .form-actions { display: flex; gap: 8px; margin-top: auto; padding-top: 13px; }
    .button {
      min-height: 38px; border-radius: 11px; padding: 0 14px; cursor: pointer;
      border: 1px solid var(--border-strong); background: var(--surface); font-size: 11px; font-weight: 720;
      transition: transform .15s ease, background .15s ease, border-color .15s ease, opacity .15s ease;
    }
    .button:hover:not(:disabled) { transform: translateY(-1px); background: var(--surface-3); }
    .button:disabled { cursor: not-allowed; opacity: .45; }
    .button.primary { flex: 1; border-color: var(--brand); background: var(--brand); color: #fff; }
    .button.primary:hover:not(:disabled) { background: var(--brand-strong); }
    .button.ghost { color: var(--text-2); }
    .button.danger { border-color: color-mix(in srgb, var(--danger) 40%, var(--border)); color: var(--danger); }

    .plan-summary { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; margin-bottom: 13px; }
    .summary-card { padding: 11px 12px; border: 1px solid var(--border); border-radius: 13px; background: var(--surface-2); }
    .summary-label { color: var(--text-3); font-size: 9px; letter-spacing: .04em; text-transform: uppercase; font-weight: 700; }
    .summary-value { margin-top: 4px; font-size: 16px; line-height: 1.1; font-weight: 760; font-variant-numeric: tabular-nums; }
    .summary-note { margin-top: 4px; color: var(--text-2); font-size: 9px; }

    .data-card { border: 1px solid var(--border); border-radius: 14px; overflow: hidden; background: var(--surface-2); }
    .data-card + .data-card { margin-top: 10px; }
    .data-card-header { padding: 10px 12px; display: flex; align-items: center; justify-content: space-between; gap: 9px; border-bottom: 1px solid var(--border); }
    .data-card-title { font-size: 10px; font-weight: 750; text-transform: uppercase; letter-spacing: .045em; }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 10px; }
    th { padding: 8px 10px; text-align: left; color: var(--text-3); font-size: 8px; letter-spacing: .055em; text-transform: uppercase; font-weight: 750; background: var(--surface-3); white-space: nowrap; }
    td { padding: 9px 10px; border-top: 1px solid var(--border); white-space: nowrap; }
    tbody tr:first-child td { border-top: 0; }
    tbody tr:hover { background: color-mix(in srgb, var(--brand-soft) 40%, transparent); }
    .route { display: flex; align-items: center; gap: 6px; font-weight: 650; }
    .route-arrow { color: var(--brand); }
    .shortage-value { color: var(--danger); font-weight: 720; }
    .impact-positive { color: var(--success); font-weight: 720; }
    .text-muted { color: var(--text-3); }
    .plan-footer { margin-top: auto; padding-top: 12px; display: flex; align-items: center; justify-content: space-between; gap: 10px; color: var(--text-3); font-size: 9px; }
    .plan-footer strong { color: var(--text-2); }

    .modal-backdrop {
      position: fixed; inset: 0; z-index: 100; display: none; padding: 22px;
      background: rgba(3, 12, 14, .64); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
      opacity: 0; transition: opacity .18s ease;
    }
    .modal-backdrop.open { display: grid; place-items: center; opacity: 1; }
    .modal {
      width: min(1180px, 100%); max-height: calc(100vh - 44px); overflow: hidden; display: flex; flex-direction: column;
      border: 1px solid var(--border); border-radius: 24px; background: var(--surface); box-shadow: var(--shadow-lg);
      transform: translateY(8px) scale(.99); transition: transform .18s ease;
    }
    .modal-backdrop.open .modal { transform: none; }
    .modal-header {
      display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 17px 20px;
      border-bottom: 1px solid var(--border); background: var(--surface-2);
    }
    .modal-heading { display: flex; align-items: center; gap: 11px; }
    .modal-title { margin: 0; font-size: 16px; }
    .modal-subtitle { margin: 3px 0 0; color: var(--text-2); font-size: 10px; }
    .close-button { width: auto; padding: 0 12px; display: flex; gap: 7px; font-size: 11px; font-weight: 700; }
    .close-button svg { width: 15px; height: 15px; }
    .modal-body { overflow: auto; padding: 20px; }
    .modal .metrics { gap: 12px; }
    .modal .metric-card { padding: 17px; }
    .modal .resource-layout { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .modal .resource-section { margin: 0; }
    .modal .incident-form { min-height: 520px; }
    .modal textarea { min-height: 150px; }
    .modal .interpretation-body { min-height: 165px; }
    .modal .plan-summary { grid-template-columns: repeat(4, 1fr); }
    .modal .data-card + .data-card { margin-top: 14px; }
    .modal table { font-size: 12px; }
    .modal th { font-size: 9px; padding: 10px 12px; }
    .modal td { padding: 12px; }

    .toast-region { position: fixed; right: 22px; bottom: 22px; z-index: 150; display: grid; gap: 8px; pointer-events: none; }
    .toast {
      width: min(360px, calc(100vw - 32px)); display: flex; align-items: flex-start; gap: 10px; padding: 12px 14px;
      border: 1px solid var(--border); border-radius: 13px; background: var(--surface); box-shadow: var(--shadow-md);
      animation: toast-in .22s ease both; pointer-events: auto;
    }
    @keyframes toast-in { from { opacity: 0; transform: translateY(9px); } }
    .toast-icon { width: 24px; height: 24px; flex: 0 0 auto; display: grid; place-items: center; border-radius: 8px; color: var(--success); background: var(--success-soft); }
    .toast-icon svg { width: 13px; height: 13px; }
    .toast strong { display: block; font-size: 11px; }
    .toast span { display: block; margin-top: 2px; color: var(--text-2); font-size: 10px; }

    .connection-banner {
      display: none; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 14px; padding: 10px 13px;
      border: 1px solid color-mix(in srgb, var(--warning) 35%, var(--border)); border-radius: 12px;
      background: var(--warning-soft); color: var(--warning); font-size: 11px;
    }
    .connection-banner.visible { display: flex; }

    @media (max-width: 1120px) {
      .dashboard-grid { grid-template-columns: 1fr 1fr; }
      .panel:last-child { grid-column: 1 / -1; min-height: auto; }
      .panel { min-height: 640px; }
      .plan-content-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
      .plan-content-grid .data-card + .data-card { margin-top: 0; }
      .plan-content-grid .data-card:last-child { grid-column: 1 / -1; }
      .plan-footer { margin-top: 12px; }
    }
    @media (max-width: 760px) {
      :root { --header-height: 66px; }
      .app-header { padding: 0 16px; }
      .brand-mark { width: 38px; height: 38px; }
      .brand-tagline, .sync-label { display: none; }
      .sync-block { min-width: 0; padding: 8px 10px; }
      .sync-time { margin: 0; font-size: 10px; }
      .main-shell { padding: 19px 14px 28px; }
      .command-bar { align-items: flex-start; flex-direction: column; gap: 12px; }
      .status-pills { justify-content: flex-start; }
      .dashboard-grid { grid-template-columns: 1fr; }
      .panel, .panel:last-child { grid-column: auto; min-height: auto; }
      .panel-content { min-height: 525px; }
      .plan-content-grid { display: block; }
      .plan-content-grid .data-card + .data-card { margin-top: 10px; }
      .modal-backdrop { padding: 0; }
      .modal { width: 100%; max-height: 100vh; height: 100vh; border: 0; border-radius: 0; }
      .modal-body { padding: 15px; }
      .modal .resource-layout { grid-template-columns: 1fr; }
      .modal .plan-summary { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 460px) {
      .brand-name { font-size: 16px; }
      .header-actions { gap: 7px; }
      .sync-block { display: none; }
      .metrics { grid-template-columns: 1fr 1fr; }
      .metric-card:first-child { grid-column: 1 / -1; }
      .panel-header { padding: 14px; }
      .panel-content { padding: 13px; }
      .extraction-grid { grid-template-columns: 1fr; }
      .modal .plan-summary { grid-template-columns: 1fr; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { scroll-behavior: auto !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; }
    }
  </style>
</head>
<body>
  <header class="app-header">
    <div class="brand" aria-label="CrisisOps home">
      <div class="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3.2c3.7 2.9 6.8 3.2 6.8 3.2v5.2c0 4.2-2.8 7.5-6.8 9.2-4-1.7-6.8-5-6.8-9.2V6.4S8.3 6.1 12 3.2Z"/><path d="M8.7 12h6.6M12 8.7v6.6"/></svg>
      </div>
      <div class="brand-copy">
        <h1 class="brand-name">CrisisOps</h1>
        <div class="brand-tagline">AI-powered disaster food distribution</div>
      </div>
    </div>
    <div class="header-actions">
      <div class="sync-block" title="Local demonstration data is active">
        <span class="sync-dot" aria-hidden="true"></span>
        <div><div class="sync-label">Last updated</div><div class="sync-time" id="last-updated">—</div></div>
      </div>
      <button class="icon-button" id="refresh-button" type="button" aria-label="Refresh command center" title="Refresh data">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20 11a8.1 8.1 0 1 0 1 4"/><path d="M20 4v7h-7"/></svg>
      </button>
      <button class="icon-button" id="theme-toggle" type="button" aria-label="Switch to dark mode" title="Change color theme"></button>
    </div>
  </header>

  <main class="main-shell">
    <div class="command-bar">
      <div>
        <div class="eyebrow">Operations dashboard</div>
        <h2 class="page-title">Food Distribution Command Center</h2>
        <p class="page-description">Monitor supply, interpret disruptions, and coordinate equitable relief.</p>
      </div>
      <div class="status-pills" aria-label="System status">
        <div class="status-pill"><span class="mini-dot"></span><strong>Operations active</strong></div>
        <div class="status-pill">Scenario <strong>Bay Area · Demo</strong></div>
      </div>
    </div>

    <div class="connection-banner" id="connection-banner" role="status">
      <span><strong>Demo mode:</strong> No backend API is implemented in this repository. Changes are kept in this browser session.</span>
      <button class="sample-button" id="dismiss-banner" type="button">Dismiss</button>
    </div>

    <div class="dashboard-grid">
      <section class="panel" aria-labelledby="state-title">
        <header class="panel-header">
          <div class="panel-heading">
            <div class="panel-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 20V9l8-5 8 5v11"/><path d="M8 20v-7h8v7M2 20h20"/></svg></div>
            <div><h3 class="panel-title" id="state-title">Current State</h3><p class="panel-subtitle">Warehouses, inventory, and active shelters</p></div>
          </div>
          <button class="expand-button" type="button" data-expand="state" aria-label="Expand current state" title="Expand panel"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/></svg></button>
        </header>
        <div class="panel-content" id="state-content"></div>
      </section>

      <section class="panel" aria-labelledby="incident-title">
        <header class="panel-header">
          <div class="panel-heading">
            <div class="panel-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.7 2.7 17a2 2 0 0 0 1.7 3h15.2a2 2 0 0 0 1.7-3L13.7 3.7a2 2 0 0 0-3.4 0Z"/></svg></div>
            <div><h3 class="panel-title" id="incident-title">Report Incident</h3><p class="panel-subtitle">Describe disruptions for AI-assisted review</p></div>
          </div>
          <button class="expand-button" type="button" data-expand="incident" aria-label="Expand incident report" title="Expand panel"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/></svg></button>
        </header>
        <div class="panel-content"><div id="incident-content"></div></div>
      </section>

      <section class="panel" aria-labelledby="plan-title">
        <header class="panel-header">
          <div class="panel-heading">
            <div class="panel-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="6" cy="18" r="2"/><circle cx="18" cy="6" r="2"/><path d="M8 18h3a3 3 0 0 0 3-3V9a3 3 0 0 1 3-3h-1M6 16V6h8"/></svg></div>
            <div><h3 class="panel-title" id="plan-title">Revise Plan</h3><p class="panel-subtitle">Review allocation impacts after an incident</p></div>
          </div>
          <button class="expand-button" type="button" data-expand="plan" aria-label="Expand revised plan" title="Expand panel"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/></svg></button>
        </header>
        <div class="panel-content" id="plan-content"></div>
      </section>
    </div>
  </main>

  <div class="modal-backdrop" id="modal-backdrop" aria-hidden="true">
    <section class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <header class="modal-header">
        <div class="modal-heading"><div class="panel-icon" id="modal-icon" aria-hidden="true"></div><div><h2 class="modal-title" id="modal-title"></h2><p class="modal-subtitle" id="modal-subtitle"></p></div></div>
        <button class="icon-button close-button" id="modal-close" type="button"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m6 6 12 12M18 6 6 18"/></svg> Close</button>
      </header>
      <div class="modal-body" id="modal-body"></div>
    </section>
  </div>
  <div class="toast-region" id="toast-region" aria-live="polite" aria-atomic="true"></div>

  <script>
    'use strict';

    const ICONS = {
      sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41"/></svg>',
      moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20.5 15.2A8.5 8.5 0 0 1 8.8 3.5 8.5 8.5 0 1 0 20.5 15.2Z"/></svg>',
      box: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m21 8-9 5-9-5 9-5 9 5Z"/><path d="m3 8 9 5 9-5v8l-9 5-9-5V8Z"/></svg>',
      warehouse: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 21V8l9-5 9 5v13M7 21v-8h10v8M7 17h10"/></svg>',
      shelter: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m3 11 9-8 9 8M5 10v11h14V10M9 21v-7h6v7"/></svg>',
      spark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m12 3 1.2 4.1L17 9l-3.8 1.9L12 15l-1.2-4.1L7 9l3.8-1.9L12 3ZM5 15l.8 2.2L8 18l-2.2.8L5 21l-.8-2.2L2 18l2.2-.8L5 15ZM19 13l.7 1.8 1.8.7-1.8.7L19 18l-.7-1.8-1.8-.7 1.8-.7L19 13Z"/></svg>',
      arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
      check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m5 12 4 4L19 6"/></svg>'
    };

    const state = {
      incidentConfirmed: false,
      analyzed: false,
      extraction: null,
      activeModal: null,
      warehouses: [
        { name: 'North Bay Depot', location: 'Santa Rosa · Sonoma Co.', kits: 38, capacity: 50, status: 'Operational' },
        { name: 'South Bay Hub', location: 'San José · Santa Clara Co.', kits: 52, capacity: 60, status: 'Operational' }
      ],
      shelters: [
        { name: 'Harbor Community Center', location: 'Oakland · 164 residents', need: 30, priority: 'High' },
        { name: 'Mission Civic Shelter', location: 'San Francisco · 112 residents', need: 24, priority: 'Medium' },
        { name: 'Eastside Relief Center', location: 'San José · 138 residents', need: 36, priority: 'High' }
      ],
      assignments: [
        { from: 'North Bay Depot', to: 'Harbor Center', kits: 28, eta: '42 min', status: 'Ready' },
        { from: 'South Bay Hub', to: 'Mission Civic', kits: 24, eta: '51 min', status: 'Ready' },
        { from: 'South Bay Hub', to: 'Eastside Relief', kits: 34, eta: '24 min', status: 'Ready' }
      ],
      shortages: [
        { shelter: 'Harbor Community Center', requested: 30, allocated: 28, gap: 2 },
        { shelter: 'Mission Civic Shelter', requested: 24, allocated: 24, gap: 0 },
        { shelter: 'Eastside Relief Center', requested: 36, allocated: 34, gap: 2 }
      ]
    };

    const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
    const qs = (selector, root = document) => root.querySelector(selector);
    const qsa = (selector, root = document) => [...root.querySelectorAll(selector)];

    function formatTime(date = new Date()) {
      return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }).format(date);
    }

    function stateMarkup(expanded = false) {
      const available = state.warehouses.reduce((sum, item) => sum + item.kits, 0);
      const warehouses = state.warehouses.map(item => `
        <div class="resource-item">
          <div><div class="resource-name">${escapeHtml(item.name)}</div><div class="resource-meta">${escapeHtml(item.location)} · ${escapeHtml(item.status)}</div></div>
          <div class="inventory"><div class="inventory-value">${item.kits} / ${item.capacity} kits</div><div class="progress"><span style="width:${Math.round(item.kits / item.capacity * 100)}%"></span></div></div>
        </div>`).join('');
      const shelters = state.shelters.map(item => `
        <div class="resource-item">
          <div><div class="resource-name">${escapeHtml(item.name)}</div><div class="resource-meta">${escapeHtml(item.location)}</div></div>
          <div><span class="tag ${item.priority === 'High' ? 'warning' : 'neutral'}">${escapeHtml(item.priority)} priority</span></div>
        </div>`).join('');
      return `
        <div class="metrics">
          <div class="metric-card"><div class="metric-top"><span class="metric-label">Available kits</span><span class="metric-icon">${ICONS.box}</span></div><div class="metric-value">${available}<span class="metric-unit">kits</span></div><div class="metric-delta">100% accounted for</div></div>
          <div class="metric-card"><div class="metric-top"><span class="metric-label">Warehouses</span><span class="metric-icon">${ICONS.warehouse}</span></div><div class="metric-value">${state.warehouses.length}</div><div class="metric-delta">All operational</div></div>
          <div class="metric-card"><div class="metric-top"><span class="metric-label">Shelters</span><span class="metric-icon">${ICONS.shelter}</span></div><div class="metric-value">${state.shelters.length}</div><div class="metric-delta">414 residents</div></div>
        </div>
        <div class="${expanded ? 'resource-layout' : ''}">
          <section class="resource-section"><div class="section-head"><h4 class="section-title">Warehouses</h4><span class="section-count">Live inventory</span></div><div class="resource-list">${warehouses}</div></section>
          <section class="resource-section"><div class="section-head"><h4 class="section-title">Active shelters</h4><span class="section-count">By priority</span></div><div class="resource-list">${shelters}</div></section>
        </div>`;
    }

    function extractionMarkup() {
      if (!state.analyzed || !state.extraction) return '<div class="empty-ai"><div>Describe an incident, then select <strong>Analyze incident</strong><br>to review structured operational details.</div></div>';
      const e = state.extraction;
      return `<div class="extraction-grid">
        <div class="extraction-item"><span>Incident type</span><strong>${escapeHtml(e.type)}</strong></div>
        <div class="extraction-item"><span>Severity</span><strong><span class="tag ${e.severity === 'Critical' ? 'danger' : 'warning'}">${escapeHtml(e.severity)}</span></strong></div>
        <div class="extraction-item"><span>Affected location</span><strong>${escapeHtml(e.location)}</strong></div>
        <div class="extraction-item"><span>Estimated delay</span><strong>${escapeHtml(e.delay)}</strong></div>
        <div class="extraction-item"><span>Resource impact</span><strong>${escapeHtml(e.impact)}</strong></div>
        <div class="extraction-item"><span>Recommended action</span><strong>${escapeHtml(e.action)}</strong></div>
      </div>`;
    }

    function incidentMarkup(context = 'main') {
      const suffix = context === 'modal' ? '-modal' : '';
      return `<form class="incident-form" data-incident-form="${context}">
        <div class="field-label-row"><label class="field-label" for="incident-text${suffix}">Enter incident description</label><span class="field-hint">Plain language · Be specific</span></div>
        <div class="textarea-wrap">
          <textarea id="incident-text${suffix}" maxlength="800" aria-describedby="incident-help${suffix}" placeholder="Example: Flooding has closed Highway 880 near Oakland, delaying the North Bay delivery by approximately two hours."></textarea>
          <span class="sr-only" id="incident-help${suffix}">Describe location, disruption, delay, and affected supplies when known.</span>
          <div class="textarea-actions"><button class="sample-button" type="button" data-sample>Use sample incident</button><button class="analyze-button" type="submit">${ICONS.spark} Analyze incident</button></div>
        </div>
        <section class="interpretation" aria-live="polite">
          <div class="interpretation-header"><div class="ai-mark">${ICONS.spark}</div><div><div class="ai-title">AI interpretation</div><div class="ai-subtitle">Review extracted information before confirmation</div></div>${state.analyzed ? '<span class="confidence">92% confidence</span>' : ''}</div>
          <div class="interpretation-body">${extractionMarkup()}</div>
        </section>
        <div class="form-actions"><button class="button primary" type="button" data-confirm ${state.analyzed ? '' : 'disabled'}>${state.incidentConfirmed ? 'Incident confirmed' : 'Confirm incident'}</button><button class="button ghost" type="button" data-cancel>Clear</button></div>
      </form>`;
    }

    function planMarkup(expanded = false) {
      const assigned = state.assignments.reduce((sum, item) => sum + item.kits, 0);
      const gap = state.shortages.reduce((sum, item) => sum + item.gap, 0);
      const assignments = state.assignments.map(item => `<tr><td><span class="route">${escapeHtml(item.from)} <span class="route-arrow">→</span> ${escapeHtml(item.to)}</span></td><td><strong>${item.kits}</strong></td><td>${escapeHtml(item.eta)}</td><td><span class="tag success">${escapeHtml(item.status)}</span></td></tr>`).join('');
      const shortages = state.shortages.map(item => `<tr><td><strong>${escapeHtml(item.shelter)}</strong></td><td>${item.requested}</td><td>${item.allocated}</td><td class="${item.gap ? 'shortage-value' : 'impact-positive'}">${item.gap ? '−' + item.gap : 'Covered'}</td></tr>`).join('');
      const impact = `
        <tr><td>Coverage rate</td><td class="text-muted">91%</td><td class="impact-positive">96%</td><td class="impact-positive">+5 pts</td></tr>
        <tr><td>Average delivery time</td><td class="text-muted">54 min</td><td class="impact-positive">39 min</td><td class="impact-positive">−15 min</td></tr>
        <tr><td>Unassigned kits</td><td class="text-muted">12</td><td class="impact-positive">4</td><td class="impact-positive">−8</td></tr>`;
      return `
        <div class="plan-summary">
          <div class="summary-card"><div class="summary-label">Kits assigned</div><div class="summary-value">${assigned} / 90</div><div class="summary-note">Across 3 active routes</div></div>
          <div class="summary-card"><div class="summary-label">Remaining gap</div><div class="summary-value" style="color:var(--danger)">${gap} kits</div><div class="summary-note">2 shelters partially covered</div></div>
          ${expanded ? '<div class="summary-card"><div class="summary-label">Est. completion</div><div class="summary-value">51 min</div><div class="summary-note">Longest active route</div></div><div class="summary-card"><div class="summary-label">Coverage</div><div class="summary-value" style="color:var(--success)">96%</div><div class="summary-note">5 points above prior plan</div></div>' : ''}
        </div>
        <div class="plan-content-grid">
          <section class="data-card"><div class="data-card-header"><span class="data-card-title">Food assignments</span><span class="tag success">3 routes</span></div><div class="table-wrap"><table><thead><tr><th>Route</th><th>Kits</th><th>ETA</th><th>Status</th></tr></thead><tbody>${assignments}</tbody></table></div></section>
          <section class="data-card"><div class="data-card-header"><span class="data-card-title">Shelter shortages</span><span class="tag warning">4-kit gap</span></div><div class="table-wrap"><table><thead><tr><th>Shelter</th><th>Need</th><th>Assigned</th><th>Gap</th></tr></thead><tbody>${shortages}</tbody></table></div></section>
          <section class="data-card"><div class="data-card-header"><span class="data-card-title">Before vs. revised plan</span><span class="tag success">Improved</span></div><div class="table-wrap"><table><thead><tr><th>Metric</th><th>Before</th><th>Revised</th><th>Impact</th></tr></thead><tbody>${impact}</tbody></table></div></section>
        </div>
        <div class="plan-footer"><span>${state.incidentConfirmed ? '<strong>Plan revised for confirmed incident</strong>' : 'Baseline allocation · Confirm an incident to revise'}</span><span>Optimizer preview · Demo</span></div>`;
    }

    function renderAll() {
      qs('#state-content').innerHTML = stateMarkup(false);
      qs('#incident-content').innerHTML = incidentMarkup('main');
      qs('#plan-content').innerHTML = planMarkup(false);
      bindIncidentControls(qs('#incident-content'));
      qs('#last-updated').textContent = formatTime();
      if (state.activeModal) renderModalContent(state.activeModal);
    }

    function analyzeIncident(text) {
      const normalized = text.toLowerCase();
      const severe = /closed|destroyed|blocked|critical|collapse|wildfire/.test(normalized);
      const locationMatch = text.match(/(?:near|at|in)\s+([A-Z][\w]*(?:\s+[A-Z0-9][\w-]*){0,3})/);
      const delayMatch = text.match(/(?:approximately|about|for)?\s*(\d+)\s*(hour|hours|minute|minutes)/i);
      state.extraction = {
        type: /flood|water/.test(normalized) ? 'Flooding / route closure' : /fire|smoke/.test(normalized) ? 'Fire disruption' : /vehicle|truck|breakdown/.test(normalized) ? 'Vehicle disruption' : 'Distribution disruption',
        severity: severe ? 'Critical' : 'High',
        location: locationMatch ? locationMatch[1] : 'Location requires confirmation',
        delay: delayMatch ? `${delayMatch[1]} ${delayMatch[2]}` : 'Approximately 2 hours',
        impact: /warehouse|depot|inventory/.test(normalized) ? 'Warehouse availability' : '1 delivery route · 28 kits',
        action: severe ? 'Reroute via South Bay Hub' : 'Monitor and adjust dispatch'
      };
      state.analyzed = true;
    }

    function bindIncidentControls(root) {
      if (!root) return;
      const form = qs('[data-incident-form]', root);
      if (!form) return;
      const textarea = qs('textarea', form);
      form.addEventListener('submit', event => {
        event.preventDefault();
        const text = textarea.value.trim();
        if (text.length < 12) { showToast('More detail needed', 'Include a location and a short description of the disruption.', 'warning'); textarea.focus(); return; }
        analyzeIncident(text);
        const context = form.dataset.incidentForm;
        if (context === 'main') qs('#incident-content').innerHTML = incidentMarkup('main');
        else qs('#modal-body').innerHTML = incidentMarkup('modal');
        const refreshedRoot = context === 'main' ? qs('#incident-content') : qs('#modal-body');
        bindIncidentControls(refreshedRoot);
        qs('textarea', refreshedRoot).value = text;
        showToast('Incident interpreted', 'Review the extracted details, then confirm the incident.');
      });
      qs('[data-sample]', form).addEventListener('click', () => {
        textarea.value = 'Flooding has closed Highway 880 near Oakland, delaying the North Bay delivery by approximately 2 hours and affecting 28 food kits.';
        textarea.focus();
      });
      qs('[data-cancel]', form).addEventListener('click', () => {
        state.analyzed = false; state.extraction = null; state.incidentConfirmed = false;
        renderAll(); showToast('Report cleared', 'No incident changes were applied.', 'neutral');
      });
      qs('[data-confirm]', form).addEventListener('click', () => {
        if (!state.analyzed) return;
        state.incidentConfirmed = true;
        state.assignments[0] = { ...state.assignments[0], from: 'South Bay Hub', eta: '48 min', status: 'Rerouted' };
        renderAll();
        showToast('Incident confirmed', 'The allocation preview has been revised for the reported disruption.');
      });
    }

    const modalMetadata = {
      state: { title: 'Current State', subtitle: 'Detailed overview of warehouses, inventory, and active shelters', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 20V9l8-5 8 5v11"/><path d="M8 20v-7h8v7M2 20h20"/></svg>' },
      incident: { title: 'Report Incident', subtitle: 'Report a disruption and review AI-extracted operational details', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.7 2.7 17a2 2 0 0 0 1.7 3h15.2a2 2 0 0 0 1.7-3L13.7 3.7a2 2 0 0 0-3.4 0Z"/></svg>' },
      plan: { title: 'Revise Plan', subtitle: 'Allocation assignments, shortages, and incident impact comparison', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="6" cy="18" r="2"/><circle cx="18" cy="6" r="2"/><path d="M8 18h3a3 3 0 0 0 3-3V9a3 3 0 0 1 3-3h-1M6 16V6h8"/></svg>' }
    };

    function openModal(type) {
      state.activeModal = type;
      const modal = qs('#modal-backdrop');
      const previousFocus = document.activeElement;
      modal.dataset.previousFocus = previousFocus && previousFocus.id ? previousFocus.id : '';
      renderModalContent(type);
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      document.body.style.overflow = 'hidden';
      requestAnimationFrame(() => qs('#modal-close').focus());
    }

    function renderModalContent(type) {
      const meta = modalMetadata[type];
      qs('#modal-title').textContent = meta.title;
      qs('#modal-subtitle').textContent = meta.subtitle;
      qs('#modal-icon').innerHTML = meta.icon;
      const body = qs('#modal-body');
      body.innerHTML = type === 'state' ? stateMarkup(true) : type === 'incident' ? incidentMarkup('modal') : planMarkup(true);
      if (type === 'incident') bindIncidentControls(body);
    }

    function closeModal() {
      const modal = qs('#modal-backdrop');
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      document.body.style.overflow = '';
      state.activeModal = null;
      const previous = modal.dataset.previousFocus && document.getElementById(modal.dataset.previousFocus);
      if (previous) previous.focus();
    }

    function showToast(title, message, tone = 'success') {
      const toast = document.createElement('div');
      toast.className = 'toast';
      const iconColor = tone === 'warning' ? 'var(--warning)' : tone === 'neutral' ? 'var(--text-2)' : 'var(--success)';
      const iconBg = tone === 'warning' ? 'var(--warning-soft)' : tone === 'neutral' ? 'var(--surface-3)' : 'var(--success-soft)';
      toast.innerHTML = `<div class="toast-icon" style="color:${iconColor};background:${iconBg}">${ICONS.check}</div><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(message)}</span></div>`;
      qs('#toast-region').appendChild(toast);
      window.setTimeout(() => toast.remove(), 4200);
    }

    function applyTheme(theme) {
      document.documentElement.dataset.theme = theme;
      const dark = theme === 'dark';
      const toggle = qs('#theme-toggle');
      toggle.innerHTML = dark ? ICONS.sun : ICONS.moon;
      toggle.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
      document.querySelector('meta[name="theme-color"]').content = dark ? '#111d20' : '#0f766e';
    }

    function initializeTheme() {
      const saved = localStorage.getItem('crisisops-theme');
      const preferred = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
      applyTheme(saved || preferred);
      qs('#theme-toggle').addEventListener('click', () => {
        const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        localStorage.setItem('crisisops-theme', theme); applyTheme(theme);
      });
    }

    function initialize() {
      initializeTheme();
      renderAll();
      qsa('[data-expand]').forEach((button, index) => {
        button.id = `expand-${button.dataset.expand}-${index}`;
        button.addEventListener('click', () => openModal(button.dataset.expand));
      });
      qs('#modal-close').addEventListener('click', closeModal);
      qs('#modal-backdrop').addEventListener('click', event => { if (event.target === event.currentTarget) closeModal(); });
      document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && state.activeModal) closeModal();
        if (event.key === 'Tab' && state.activeModal) {
          const focusable = qsa('button:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])', qs('.modal'));
          if (!focusable.length) return;
          const first = focusable[0], last = focusable[focusable.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
      });
      qs('#refresh-button').addEventListener('click', () => { qs('#last-updated').textContent = formatTime(); showToast('Command center refreshed', 'Local demonstration data is up to date.'); });
      const bannerDismissed = sessionStorage.getItem('crisisops-demo-dismissed');
      if (!bannerDismissed) qs('#connection-banner').classList.add('visible');
      qs('#dismiss-banner').addEventListener('click', () => { qs('#connection-banner').classList.remove('visible'); sessionStorage.setItem('crisisops-demo-dismissed', 'true'); });
    }

    // Stable, isolated boundary for a future API adapter without coupling the UI
    // to routes that do not yet exist in this repository.
    window.CrisisOps = Object.freeze({
      getState: () => JSON.parse(JSON.stringify(state)),
      refresh: renderAll,
      version: '1.0.0-demo'
    });

    initialize();
  </script>
</body>
</html>
'''


class CrisisOpsHandler(BaseHTTPRequestHandler):
    """Serve the single-page command center and a lightweight health route."""

    server_version = "CrisisOpsFrontend/1.0"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = self.path.split("?", 1)[0]
        if path in {"/", "/index.html"}:
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/health":
            payload = json.dumps(
                {"status": "ok", "service": "crisisops-frontend", "mode": "demo"}
            ).encode("utf-8")
            self._send(payload, "application/json; charset=utf-8")
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        payload = json.dumps({"error": "Not found"}).encode("utf-8")
        self._send(payload, "application/json; charset=utf-8", status=404)

    def _send(self, payload: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        """Keep normal request logging concise and recognizable."""
        print(f"[CrisisOps] {self.address_string()} - {format % args}")


def validate_page() -> None:
    """Perform a dependency-free structural check for build pipelines."""
    required_markers = (
        "Food Distribution Command Center",
        'id="state-content"',
        'id="incident-content"',
        'id="plan-content"',
        'id="theme-toggle"',
        "window.CrisisOps",
        "</html>",
    )
    missing = [marker for marker in required_markers if marker not in PAGE]
    if missing:
        raise RuntimeError(f"Frontend page is missing markers: {', '.join(missing)}")
    print(f"CrisisOps frontend check passed ({len(PAGE):,} bytes).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CrisisOps web frontend")
    parser.add_argument("--host", default=HOST, help=f"bind host (default: {HOST})")
    parser.add_argument("--port", default=PORT, type=int, help=f"bind port (default: {PORT})")
    parser.add_argument("--check", action="store_true", help="validate the embedded page and exit")
    args = parser.parse_args()

    if args.check:
        validate_page()
        return

    server = ThreadingHTTPServer((args.host, args.port), CrisisOpsHandler)
    print(f"CrisisOps frontend running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping CrisisOps frontend.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
