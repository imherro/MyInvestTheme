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

  function combinedScore(point) {
    return firstNumber([point.combined_score, point.combinedScore]);
  }

  function latestPoint(points) {
    return points[points.length - 1] || {};
  }

  function latestPolicyScore(theme) {
    return policyScore(latestPoint(theme.points || [])) ?? -1;
  }

  function latestChartScore(theme, scoreAccessor) {
    return scoreAccessor(latestPoint(theme.points || []));
  }

  function scoreSortValue(value) {
    return value === null ? -1 : value;
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

  function niceCeil(value) {
    if (!Number.isFinite(value) || value <= 0) return 1;
    const exponent = Math.floor(Math.log10(value));
    const scale = Math.pow(10, exponent);
    const normalized = value / scale;
    const nice = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((step) => normalized <= step) || 10;
    return nice * scale;
  }

  function niceStep(value) {
    if (!Number.isFinite(value) || value <= 0) return 1;
    const exponent = Math.floor(Math.log10(value));
    const scale = Math.pow(10, exponent);
    const normalized = value / scale;
    const nice = [1, 2, 2.5, 5, 10].find((step) => normalized <= step) || 10;
    return nice * scale;
  }

  function chartYMax(themes, scoreAccessor) {
    const scores = themes
      .flatMap((theme) => (theme.points || []).map(scoreAccessor))
      .filter((score) => score !== null && Number.isFinite(score));
    const maxScore = Math.max(0, ...scores);
    return niceCeil(maxScore * 1.12);
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
        return `<circle cx="${xFor(item.point.x).toFixed(2)}" cy="${yFor(item.score).toFixed(2)}" r="4.8" fill="${color}"><title>${escapeHtml(title)}</title></circle>`;
      })
      .join("");
  }

  function renderTrendChart(themes, labels, scoreAccessor, title, label, yMax, availableWidth) {
    const width = Math.max(320, Math.floor(availableWidth || 1120));
    const height = 760;
    const showLegend = width >= 980;
    const pad = showLegend
      ? { left: 76, right: 360, top: 72, bottom: 92 }
      : { left: 68, right: 28, top: 72, bottom: 92 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const xFor = (label) => pad.left + (labels.length <= 1 ? plotW / 2 : (labels.indexOf(label) / (labels.length - 1)) * plotW);
    const yFor = (score) => pad.top + (1 - clamp(score, 0, yMax) / yMax) * plotH;
    const axisTicks = [];
    const yStep = niceStep(yMax / 5);
    for (let value = 0; value <= yMax; value += yStep) axisTicks.push(value);
    if (axisTicks[axisTicks.length - 1] !== yMax) axisTicks.push(yMax);
    const labelStep = Math.max(1, Math.ceil(labels.length / (width >= 760 ? 6 : 3)));
    const legendThemes = [...themes].sort((a, b) => {
      const scoreDiff = scoreSortValue(latestChartScore(b, scoreAccessor)) - scoreSortValue(latestChartScore(a, scoreAccessor));
      return scoreDiff || String(a.theme || "").localeCompare(String(b.theme || ""), "zh-Hans-CN");
    });

    return `
      <svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(title)}">
        <rect x="0" y="0" width="${width}" height="${height}" fill="#fbfcfd" />
        <text x="${pad.left}" y="42" font-size="22" font-weight="700" fill="#172033">${escapeHtml(title)}</text>
        <text x="${pad.left + 280}" y="42" font-size="16" fill="#667085">Y轴 0-${formatScore(yMax)}</text>
        ${axisTicks.map((value) => {
          const y = yFor(value);
          return `<line x1="${pad.left}" y1="${y}" x2="${pad.left + plotW}" y2="${y}" stroke="#e4e7ec" /><text x="${pad.left - 12}" y="${y + 6}" text-anchor="end" font-size="16" fill="#667085">${Math.round(value)}</text>`;
        }).join("")}
        ${labels.map((timeLabel, index) => {
          if (index !== 0 && index !== labels.length - 1 && index % labelStep !== 0) return "";
          const x = xFor(timeLabel);
          return `<text x="${x}" y="${height - 38}" text-anchor="middle" font-size="16" fill="#667085">${escapeHtml(timeLabel)}</text>`;
        }).join("")}
        <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${pad.top + plotH}" stroke="#98a2b3" />
        <line x1="${pad.left}" y1="${pad.top + plotH}" x2="${pad.left + plotW}" y2="${pad.top + plotH}" stroke="#98a2b3" />
        ${themes.map((theme, index) => {
          const color = theme.color || themeColor(index);
          const points = theme.points || [];
          return `<path d="${pathFor(points, scoreAccessor, xFor, yFor)}" fill="none" stroke="${color}" stroke-width="3.2" />${circlesFor(points, scoreAccessor, xFor, yFor, color, label, theme.theme)}`;
        }).join("")}
        ${showLegend ? legendThemes.map((theme, index) => {
          const y = pad.top + index * 42;
          const color = theme.color || themeColor(index);
          const latest = scoreAccessor(latestPoint(theme.points || []));
          return `<line x1="${pad.left + plotW + 28}" y1="${y}" x2="${pad.left + plotW + 58}" y2="${y}" stroke="${color}" stroke-width="4.5" /><text x="${pad.left + plotW + 70}" y="${y + 6}" font-size="16" fill="#172033">${escapeHtml(theme.theme)} ${formatScore(latest)}</text>`;
        }).join("") : ""}
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
    const isTaxonomyV2 = payload && payload.taxonomy_version === "theme_taxonomy_v2";
    const rankingScore = isTaxonomyV2 ? (theme) => latestChartScore(theme, combinedScore) ?? -1 : latestPolicyScore;
    const themes = (payload.themes || [])
      .map((theme) => ({
        ...theme,
        points: (theme.points || []).filter((point) => policyScore(point) !== null || marketScore(point) !== null),
      }))
      .filter((theme) => theme.points.length)
      .sort((a, b) => rankingScore(b) - rankingScore(a))
      .slice(0, 8)
      .map((theme, index) => ({ ...theme, color: themeColor(index) }));

    if (!themes.length) {
      container.innerHTML = '<div class="chart-empty">暂无曲线数据。</div>';
      return;
    }

    const labels = Array.from(new Set(themes.flatMap((theme) => theme.points.map((point) => point.x))));
    const chartWidth = Math.max(320, Math.floor(container.clientWidth || 1120));
    const policyYMax = chartYMax(themes, policyScore);
    const marketYMax = chartYMax(themes, marketScore);
    const policyTitle = isTaxonomyV2 ? "二级主线政策映射分历史变化" : "政策主线分历史变化";
    const marketTitle = isTaxonomyV2 ? "二级主线市场热度分历史变化" : "市场热度观察分历史变化";
    const rankingText = isTaxonomyV2 ? "显示最新综合观察分前 8 个二级主题" : "显示最新政策主线分前 8 个主题";
    const latestScores = themes.flatMap((theme) => {
      const point = latestPoint(theme.points || []);
      return [policyScore(point), marketScore(point)].filter((score) => score !== null);
    });
    const strengthScaleMax = Math.max(100, Math.ceil(Math.max(...latestScores) / 10) * 10);

    container.innerHTML = `
      <div class="dual-chart">
        <div class="chart-legend">
          <span>颜色=主题，同一主题在两张图里颜色一致</span>
          <span>横轴=报告生成时间，${escapeHtml(rankingText)}</span>
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
          <span>分开看政策主线分和市场热度观察分；每张图独立缩放Y轴，右侧说明按本图最新分数降序排列</span>
        </div>
        <div class="trend-list">
          <div class="trend-card">${renderTrendChart(themes, labels, policyScore, policyTitle, "政策映射分", policyYMax, chartWidth)}</div>
          <div class="trend-card">${renderTrendChart(themes, labels, marketScore, marketTitle, "市场热度分", marketYMax, chartWidth)}</div>
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
