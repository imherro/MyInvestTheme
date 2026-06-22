(function () {
  const POLICY_COLOR = "#0f766e";
  const MARKET_COLOR = "#b54708";

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

  function circlesFor(points, scoreAccessor, xFor, yFor, color, label) {
    return points
      .map((point) => ({ point, score: scoreAccessor(point) }))
      .filter((item) => item.score !== null)
      .map((item) => {
        const title = [
          `${item.point.x}`,
          `${label} ${formatScore(item.score)}`,
          `报告 ${item.point.report_id}`,
        ].join(" / ");
        return `<circle cx="${xFor(item.point.x).toFixed(2)}" cy="${yFor(item.score).toFixed(2)}" r="3.4" fill="${color}"><title>${escapeHtml(title)}</title></circle>`;
      })
      .join("");
  }

  function renderMiniChart(theme, labels, yMax) {
    const width = 620;
    const height = 128;
    const pad = { left: 34, right: 12, top: 12, bottom: 26 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const xFor = (label) => pad.left + (labels.length <= 1 ? plotW / 2 : (labels.indexOf(label) / (labels.length - 1)) * plotW);
    const yFor = (score) => pad.top + (1 - clamp(score, 0, yMax) / yMax) * plotH;
    const mid = yMax / 2;
    const points = theme.points || [];
    const policyPath = pathFor(points, policyScore, xFor, yFor);
    const marketPath = pathFor(points, marketScore, xFor, yFor);
    const firstLabel = labels[0] || "";
    const lastLabel = labels[labels.length - 1] || "";

    return `
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(theme.theme)} 政策主线分与市场热度观察分">
        <rect x="0" y="0" width="${width}" height="${height}" fill="#fbfcfd" />
        ${[0, mid, yMax].map((value) => {
          const y = yFor(value);
          return `<line x1="${pad.left}" y1="${y}" x2="${pad.left + plotW}" y2="${y}" stroke="#e4e7ec" /><text x="${pad.left - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="#667085">${Math.round(value)}</text>`;
        }).join("")}
        <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${pad.top + plotH}" stroke="#98a2b3" />
        <line x1="${pad.left}" y1="${pad.top + plotH}" x2="${pad.left + plotW}" y2="${pad.top + plotH}" stroke="#98a2b3" />
        <path d="${policyPath}" fill="none" stroke="${POLICY_COLOR}" stroke-width="2.6" />
        <path d="${marketPath}" fill="none" stroke="${MARKET_COLOR}" stroke-width="2.4" stroke-dasharray="6 4" />
        ${circlesFor(points, policyScore, xFor, yFor, POLICY_COLOR, "政策主线分")}
        ${circlesFor(points, marketScore, xFor, yFor, MARKET_COLOR, "市场热度观察分")}
        <text x="${pad.left}" y="${height - 6}" font-size="11" fill="#667085">${escapeHtml(firstLabel)}</text>
        <text x="${pad.left + plotW}" y="${height - 6}" text-anchor="end" font-size="11" fill="#667085">${escapeHtml(lastLabel)}</text>
      </svg>`;
  }

  function renderThemeRow(theme, labels, yMax) {
    const latest = latestPoint(theme.points || []);
    const policy = policyScore(latest);
    const market = marketScore(latest);
    return `
      <div class="spark-row">
        <div class="spark-meta">
          <strong>${escapeHtml(theme.theme)}</strong>
          <span>${escapeHtml(latest.x || "")}</span>
        </div>
        <div class="spark-plot">${renderMiniChart(theme, labels, yMax)}</div>
        <div class="spark-values">
          <div><span>政策主线分</span><strong>${formatScore(policy)}</strong></div>
          <div><span>市场热度分</span><strong>${formatScore(market)}</strong></div>
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
      .slice(0, 6);

    if (!themes.length) {
      container.innerHTML = '<div class="chart-empty">暂无曲线数据。</div>';
      return;
    }

    const labels = Array.from(new Set(themes.flatMap((theme) => theme.points.map((point) => point.x))));
    const scores = themes.flatMap((theme) => theme.points.flatMap((point) => [policyScore(point), marketScore(point)]).filter((score) => score !== null));
    const yMax = Math.max(100, Math.ceil(Math.max(...scores) / 10) * 10);

    container.innerHTML = `
      <div class="dual-chart">
        <div class="chart-legend">
          <span><i class="legend-line policy"></i>绿色实线=政策主线分 mainline_score_v6 × 100</span>
          <span><i class="legend-line market"></i>橙色虚线=市场热度观察分</span>
          <span>横轴=报告生成时间，显示最新政策主线分前 6 个主题</span>
        </div>
        <div class="spark-list">
          ${themes.map((theme) => renderThemeRow(theme, labels, yMax)).join("")}
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
