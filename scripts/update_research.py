<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Stock Analysis - The Vance Report</title>
  <style>
    :root {
      --bg: #0c1014;
      --card-bg: #12181e;
      --border: #1e2630;
      --text: #e7e3d8;
      --text-muted: #8b9bb0;
      --amber: #e0a33c;
      --green: #26a69a;
      --red: #ef5350;
      --mono: monospace;
    }
    * { box-sizing: border-box; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0;
      padding: 24px;
    }
    .header {
      max-width: 1200px;
      margin: 0 auto 24px auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border);
      padding-bottom: 16px;
    }
    .back-btn {
      color: var(--amber);
      text-decoration: none;
      font-family: var(--mono);
      font-size: 14px;
      letter-spacing: 0.5px;
    }
    .back-btn:hover { text-decoration: underline; }
    .header-title-group { text-align: right; }
    .header-title { color: var(--amber); margin: 0; font-size: 26px; letter-spacing: 1px; }
    .header-sub { color: var(--text-muted); font-size: 13px; font-family: var(--mono); }

    .container { max-width: 1200px; margin: 0 auto; }

    /* Key Metrics Grid */
    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .metric-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 16px;
    }
    .metric-label {
      color: var(--text-muted);
      font-size: 11px;
      font-family: var(--mono);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 6px;
    }
    .metric-value {
      font-size: 22px;
      font-weight: 700;
      color: var(--text);
    }
    .metric-badge {
      display: inline-block;
      margin-top: 6px;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-family: var(--mono);
      font-weight: 600;
    }
    .badge-pass { background: rgba(38, 166, 154, 0.15); color: var(--green); border: 1px solid var(--green); }
    .badge-fail { background: rgba(239, 83, 80, 0.15); color: var(--red); border: 1px solid var(--red); }
    .badge-pending { background: rgba(139, 155, 176, 0.15); color: var(--text-muted); border: 1px solid var(--text-muted); }
    .badge-discount { background: rgba(224, 163, 60, 0.15); color: var(--amber); border: 1px solid var(--amber); }

    /* Main Content Layout */
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 20px;
      margin-bottom: 24px;
    }
    #tradingview_chart { height: 500px; width: 100%; }

    /* Two-Column Research Layout */
    .research-grid {
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 24px;
    }
    @media (max-width: 900px) {
      .research-grid { grid-template-columns: 1fr; }
    }

    .section-title {
      font-size: 14px;
      font-family: var(--mono);
      color: var(--amber);
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-top: 0;
      margin-bottom: 16px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 8px;
    }

    .notes-p {
      line-height: 1.6;
      color: var(--text);
      font-size: 14px;
      margin-bottom: 16px;
    }
    .notes-list {
      margin: 0;
      padding-left: 20px;
      color: var(--text-muted);
      font-size: 14px;
      line-height: 1.6;
    }
    .notes-list li { margin-bottom: 8px; }
    .data-note {
      font-size: 11px;
      color: var(--text-muted);
      margin-top: -12px;
      margin-bottom: 24px;
    }
  </style>
</head>
<body>

  <div class="header">
    <a href="index.html" class="back-btn">&larr; BACK TO SCREENER</a>
    <div class="header-title-group">
      <h1 id="ticker-title" class="header-title">-- PROFILE</h1>
      <div id="company-name" class="header-sub">Loading profile...</div>
    </div>
  </div>

  <div class="container">

    <!-- Top Stat Cards -->
    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-label">Current Market Price</div>
        <div id="m-price" class="metric-value">--</div>
        <span id="m-drop" class="metric-badge badge-discount">--</span>
      </div>
      <div class="metric-card">
        <div class="metric-label">1-Day Change</div>
        <div id="m-change" class="metric-value">--</div>
        <span id="m-change-badge" class="metric-badge badge-pending">--</span>
      </div>
      <div class="metric-card">
        <div class="metric-label">Cash Cushion Floor (CPS)</div>
        <div id="m-cps" class="metric-value">--</div>
        <span id="m-gate-badge" class="metric-badge">--</span>
      </div>
      <div class="metric-card">
        <div class="metric-label">Target Execution Zone</div>
        <div id="m-target" class="metric-value">--</div>
        <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">Risk/Reward Baseline</div>
      </div>
    </div>

    <!-- Interactive TradingView Chart -->
    <div class="card">
      <div id="tradingview_chart"></div>
    </div>

    <!-- Deep Dive Research Grid -->
    <div class="research-grid">

      <div class="card">
        <h3 class="section-title">Core Investment Thesis & Catalyst</h3>
        <p id="thesis-body" class="notes-p">Loading detailed thesis...</p>

        <h3 class="section-title" style="margin-top: 24px;">Key Catalyst Timeline</h3>
        <p id="catalyst-body" class="notes-p">Loading catalyst info...</p>
      </div>

      <div class="card">
        <h3 class="section-title">Risk & Liquidity Profile</h3>
        <ul id="risks-list" class="notes-list">
          <li>Loading risk analysis...</li>
        </ul>
      </div>

    </div>

  </div>

  <!-- TradingView Widget Script -->
  <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
  <script>
    const urlParams = new URLSearchParams(window.location.search);
    const symbol = (urlParams.get('symbol') || 'BMEA').toUpperCase();
    document.getElementById('ticker-title').innerText = symbol + ' PROFILE';

    // Initialize TradingView Widget
    new TradingView.widget({
      "width": "100%",
      "height": 500,
      "symbol": symbol,
      "interval": "D",
      "timezone": "Etc/UTC",
      "theme": "dark",
      "style": "1",
      "locale": "en",
      "toolbar_bg": "#12181e",
      "enable_publishing": false,
      "allow_symbol_change": true,
      "container_id": "tradingview_chart"
    });

    function money(v) {
      return '$' + Math.abs(v).toFixed(2);
    }
    function signed(v, decimals) {
      return (v < 0 ? '-' : '+') + Math.abs(v).toFixed(decimals);
    }

    // Fetch and populate research data. research.json only contains
    // currently-passing tickers; a symbol that no longer clears the gate
    // (or never did) won't be here, which is shown honestly below rather
    // than silently defaulting to a passing state.
    fetch('research.json')
      .then(res => res.json())
      .then(data => {
        const item = data[symbol];
        if (!item) {
          document.getElementById('company-name').innerText =
            'Not currently on the daily screen';
          document.getElementById('thesis-body').innerText =
            'This ticker isn\u2019t in today\u2019s passing list \u2014 either it hasn\u2019t cleared the gate, or it hasn\u2019t been evaluated. Check the homepage for today\u2019s full list.';
          document.getElementById('catalyst-body').innerText = '';
          document.getElementById('risks-list').innerHTML = '';
          return;
        }

        document.getElementById('company-name').innerText = item.name || symbol;

        const price = typeof item.price === 'number' ? item.price : null;
        const cps = typeof item.cps === 'number' ? item.cps : null;
        const cushion = typeof item.cash_cushion_pct === 'number' ? item.cash_cushion_pct : null;
        const drop = typeof item.five_day_drop_pct === 'number' ? item.five_day_drop_pct : null;
        const dollarChange = typeof item.dollar_change === 'number' ? item.dollar_change : null;
        const returnPct = typeof item.return_pct === 'number' ? item.return_pct : null;

        document.getElementById('m-price').innerText = price != null ? money(price) : '--';
        document.getElementById('m-drop').innerText = drop != null
          ? signed(drop, 1) + '% (5-day)'
          : '5-day drop unavailable';

        // 1-Day Change: only shown if we actually have a stored baseline
        // from yesterday's run. No fabricated number when we don't.
        const changeEl = document.getElementById('m-change');
        const changeBadge = document.getElementById('m-change-badge');
        if (dollarChange != null && returnPct != null) {
          changeEl.innerText = signed(dollarChange, 2);
          changeEl.style.color = dollarChange > 0 ? 'var(--green)' : dollarChange < 0 ? 'var(--red)' : 'var(--text)';
          changeBadge.className = 'metric-badge ' + (dollarChange > 0 ? 'badge-pass' : dollarChange < 0 ? 'badge-fail' : 'badge-pending');
          changeBadge.innerText = signed(returnPct, 1) + '% vs. prior session';
        } else {
          changeEl.innerText = '--';
          changeBadge.className = 'metric-badge badge-pending';
          changeBadge.innerText = 'Pending baseline';
        }

        document.getElementById('m-cps').innerText = cps != null ? money(cps) : '--';

        // Gatekeeper badge: PASS / FAIL / UNKNOWN, never a fabricated default.
        const gateBadge = document.getElementById('m-gate-badge');
        if (item.gate === 'PASS') {
          gateBadge.className = 'metric-badge badge-pass';
          gateBadge.innerText = (cushion != null ? cushion.toFixed(1) + '% ' : '') + 'Clears 30.0% Floor';
        } else if (item.gate === 'FAIL') {
          gateBadge.className = 'metric-badge badge-fail';
          gateBadge.innerText = (cushion != null ? cushion.toFixed(1) + '% ' : '') + 'Below 30.0% Floor';
        } else {
          gateBadge.className = 'metric-badge badge-pending';
          gateBadge.innerText = 'Balance sheet data pending';
        }

        document.getElementById('m-target').innerText = item.target_zone || '\u2014';
        document.getElementById('thesis-body').innerText = item.thesis || 'No detailed thesis available.';
        document.getElementById('catalyst-body').innerText = item.catalyst || 'No primary catalyst specified.';

        const risksList = document.getElementById('risks-list');
        if (item.risks && item.risks.length > 0) {
          risksList.innerHTML = item.risks.map(r => `<li>${r.replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}</li>`).join('');
        } else {
          risksList.innerHTML = '<li>No specific risks noted.</li>';
        }
      })
      .catch(err => {
        console.error(err);
        document.getElementById('thesis-body').innerText = 'Unable to load research.json.';
      });
  </script>
</body>
</html>
