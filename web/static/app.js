(function () {
  const POLICY_COLOR = "#0f766e";
  const MARKET_COLOR = "#b54708";
  const THEME_COLORS = ["#0f766e", "#b42318", "#175cd3", "#7a5af8", "#b54708", "#067647", "#c11574", "#475467"];

  function numberOrNull(value) {
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
  }

  function firstNumber(values) {
    for (const value of values) {
      const num = numberOrNull(value);
      if (num !== null) return num;
    }
    return null;
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatScore(value) {
    const score = numberOrNull(value);
    return score === null ? "无" : score.toFixed(1);
  }

  function policyScore(point) {
    const score = firstNumber([point.mainline_score_v6, point.default_score, point.score]);
    return score === null ? null : score * 100;
  }

  function marketScore(point) {
    return firstNumber([
      point.legacy_market_score,
      point.market_score,
      point.legacy_evidence_score,
      point.evidence_score,
    ]);
  }

  function latestPoint(points) {
    return points[points.length - 1] || {};
  }

  function latestPolicyScore(theme) {
    return policyScore(latestPoint(theme.points || [])) ?? -1;
  }

  function themeColor(index) {
    return THEME_COLORS[index % THEME_COLORS.length];
  }

  function judgement(policy, market) {
    if (policy === null || market === null) return "数据不足";
    if (policy >= 50 && market >= 72) return "政策强 + 市场确认";
    if (policy >= 50) return "政策强，等市场";
    if (market >= 72) return "市场热，政策次强";
    if (policy >= 25 && market >= 50) return "双线观察";
    return "低确认";
  }

  function pathFor(points, scoreAccessor, xFor, yFor) {
    return points
      .map((point) => ({ point, score: scoreAccessor(point) }))
      .filter((item) => item.score !== null)
      .map((item, index) => `${index === 0 ? "M" : "L"} ${xFor(item.point.x).toFixed(2)} ${yFor(item.score).toFixed(2)}`)
      .join(" ");
  }

  function circlesFor(points, scoreAccessor, xFor, yFor, color, label, themeName) {
    return points
      .map((point) => ({ point, score: scoreAccessor(point) }))
      .filter((item) => item.score !== null)
      .map((item) => {
        const title = [
          themeName,
          `${item.point.x}`,
          `${label} ${formatScore(item.score)}`,
          `报告 ${item.point.report_id}`,
        ].join(" / ");
        return `<circle cx="${xFor(item.point.x).toFixed(2)}" cy="${yFor(item.score).toFixed(2)}" r="3.4" fill="${color}"><title>${escapeHtml(title)}</title></circle>`;
      })
      .join("");
  }

  function renderTrendChart(themes, labels, scoreAccessor, title, label, yMax) {
    const width = Math.max(1020, labels.length * 76 + 260);
    const height = 360;
    const pad = { left: 48, right: 230, top: 34, bottom: 48 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const xFor = (label) => pad.left + (labels.length <= 1 ? plotW / 2 : (labels.indexOf(label) / (labels.length - 1)) * plotW);
    const yFor = (score) => pad.top + (1 - clamp(score, 0, yMax) / yMax) * plotH;
    const axisTicks = [];
    for (let value = 0; value <= yMax; value += 20) axisTicks.push(value);
    if (axisTicks[axisTicks.length - 1] !== yMax) axisTicks.push(yMax);
    const labelStep = Math.max(1, Math.ceil(labels.length / 6));

    return `
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(title)}">
        <rect x="0" y="0" width="${width}" height="${height}" fill="#fbfcfd" />
        <text x="${pad.left}" y="18" font-size="14" font-weight="700" fill="#172033">${escapeHtml(title)}</text>
        ${axisTicks.map((value) => {
          const y = yFor(value);
          return `<line x1="${pad.left}" y1="${y}" x2="${pad.left + plotW}" y2="${y}" stroke="#e4e7ec" /><text x="${pad.left - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="#667085">${Math.round(value)}</text>`;
        }).join("")}
        ${labels.map((timeLabel, index) => {
          if (index !== 0 && index !== labels.length - 1 && index % labelStep !== 0) return "";
          const x = xFor(timeLabel);
          return `<text x="${x}" y="${height - 16}" text-anchor="middle" font-size="11" fill="#667085">${escapeHtml(timeLabel)}</text>`;
        }).join("")}
        <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${pad.top + plotH}" stroke="#98a2b3" />
        <line x1="${pad.left}" y1="${pad.top + plotH}" x2="${pad.left + plotW}" y2="${pad.top + plotH}" stroke="#98a2b3" />
        ${themes.map((theme, index) => {
          const color = themeColor(index);
          const points = theme.points || [];
          return `<path d="${pathFor(points, scoreAccessor, xFor, yFor)}" fill="none" stroke="${color}" stroke-width="2.4" />${circlesFor(points, scoreAccessor, xFor, yFor, color, label, theme.theme)}`;
        }).join("")}
        ${themes.map((theme, index) => {
          const y = pad.top + index * 24;
          const color = themeColor(index);
          const latest = scoreAccessor(latestPoint(theme.points || []));
          return `<line x1="${pad.left + plotW + 22}" y1="${y}" x2="${pad.left + plotW + 42}" y2="${y}" stroke="${color}" stroke-width="3" /><text x="${pad.left + plotW + 50}" y="${y + 4}" font-size="12" fill="#172033">${escapeHtml(theme.theme)} ${formatScore(latest)}</text>`;
        }).join("")}
      </svg>`;
  }

  function renderStrengthRow(theme, scaleMax) {
    const latest = latestPoint(theme.points || []);
    const policy = policyScore(latest);
    const market = marketScore(latest);
    const policyWidth = policy === null ? 0 : clamp(policy / scaleMax, 0, 1) * 100;
    const marketWidth = market === null ? 0 : clamp(market / scaleMax, 0, 1) * 100;
    return `
      <div class="strength-row">
        <strong>${escapeHtml(theme.theme)}</strong>
        <div class="strength-bars">
          <div class="strength-track">
            <span class="strength-label">政策</span>
            <span class="strength-bar policy" style="width: ${policyWidth.toFixed(1)}%"></span>
          </div>
          <div class="strength-track">
            <span class="strength-label">热度</span>
            <span class="strength-bar market" style="width: ${marketWidth.toFixed(1)}%"></span>
          </div>
        </div>
        <div class="strength-score">
          <span>${formatScore(policy)} / ${formatScore(market)}</span>
          <em>${escapeHtml(judgement(policy, market))}</em>
        </div>
      </div>`;
  }

  function drawScoreChart(container, payload) {
    const themes = (payload.themes || [])
      .map((theme) => ({
        ...theme,
        points: (theme.points || []).filter((point) => policyScore(point) !== null || marketScore(point) !== null),
      }))
      .filter((theme) => theme.points.length)
      .sort((a, b) => latestPolicyScore(b) - latestPolicyScore(a))
      .slice(0, 8);

    if (!themes.length) {
      container.innerHTML = '<div class="chart-empty">暂无曲线数据。</div>';
      return;
    }

    const labels = Array.from(new Set(themes.flatMap((theme) => theme.points.map((point) => point.x))));
    const scores = themes.flatMap((theme) => theme.points.flatMap((point) => [policyScore(point), marketScore(point)]).filter((score) => score !== null));
    const yMax = Math.max(100, Math.ceil(Math.max(...scores) / 10) * 10);
    const latestScores = themes.flatMap((theme) => {
      const point = latestPoint(theme.points || []);
      return [policyScore(point), marketScore(point)].filter((score) => score !== null);
    });
    const strengthScaleMax = Math.max(100, Math.ceil(Math.max(...latestScores) / 10) * 10);

    container.innerHTML = `
      <div class="dual-chart">
        <div class="chart-legend">
          <span>颜色=主题，同一主题在两张图里颜色一致</span>
          <span>横轴=报告生成时间，显示最新政策主线分前 8 个主题</span>
        </div>
        <div class="strength-board">
          <div class="chart-subhead">
            <strong>最新强弱对比</strong>
            <span>横条越长，当前分数越高；格式为 政策主线分 / 市场热度分</span>
          </div>
          ${themes.map((theme) => renderStrengthRow(theme, strengthScaleMax)).join("")}
        </div>
        <div class="chart-subhead">
          <strong>时间走势</strong>
          <span>分开看政策主线分和市场热度观察分，便于比较不同主题强弱</span>
        </div>
        <div class="trend-list">
          <div class="trend-card">${renderTrendChart(themes, labels, policyScore, "政策主线分历史变化", "政策主线分", yMax)}</div>
          <div class="trend-card">${renderTrendChart(themes, labels, marketScore, "市场热度观察分历史变化", "市场热度观察分", yMax)}</div>
        </div>
      </div>`;
  }

  document.querySelectorAll("[data-source]").forEach(async (container) => {
    try {
      const response = await fetch(container.dataset.source, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      drawScoreChart(container, await response.json());
    } catch (error) {
      container.innerHTML = `<div class="chart-error">曲线数据读取失败：${escapeHtml(error.message)}</div>`;
    }
  });
})();
