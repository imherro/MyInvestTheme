(function () {
  const palette = ["#0f766e", "#b42318", "#175cd3", "#7a5af8", "#b54708", "#067647", "#c11574", "#475467"];

  function numberOrNull(value) {
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function resonanceScore(point) {
    const explicit = numberOrNull(point.resonance_score);
    if (explicit !== null) return explicit;
    const parts = [point.score, point.theme_score, point.etf_score].map(numberOrNull).filter((value) => value !== null);
    if (!parts.length) return 0;
    return parts.reduce((sum, value) => sum + value, 0) / parts.length;
  }

  function pointRadius(point) {
    const resonance = resonanceScore(point);
    const base = 4 + (clamp(resonance, 45, 100) - 45) / 55 * 5;
    return point.triple_confirmation ? base + 1.5 : base;
  }

  function pointOpacity(point) {
    const themeScore = numberOrNull(point.theme_score);
    if (themeScore === null) return 0.75;
    return 0.35 + clamp(themeScore, 0, 100) / 100 * 0.6;
  }

  function pointStroke(point) {
    const etfScore = numberOrNull(point.etf_score);
    if (point.triple_confirmation) return { color: "#111827", width: 3.4 };
    if (etfScore !== null && etfScore >= 75) return { color: "#344054", width: 2.4 };
    return { color: "#ffffff", width: 1.4 };
  }

  function formatScore(value) {
    const score = numberOrNull(value);
    return score === null ? "无" : score.toFixed(1);
  }

  function formatMainlineScore(value) {
    const score = numberOrNull(value);
    return score === null ? "无" : score.toFixed(4);
  }

  function defaultScore(point) {
    return numberOrNull(point.default_score ?? point.score);
  }

  function plotScore(point) {
    const score = defaultScore(point);
    if (score === null) return null;
    return point.default_score_field === "mainline_score_v6" ? score * 100 : score;
  }

  function drawScoreChart(container, payload) {
    const themes = (payload.themes || []).filter((item) => (item.points || []).some((p) => plotScore(p) !== null));
    if (!themes.length) {
      container.innerHTML = '<div class="chart-empty">暂无曲线数据。</div>';
      return;
    }

    const labels = Array.from(new Set(themes.flatMap((item) => item.points.map((p) => p.x))));
    const width = Math.max(900, labels.length * 150 + 220);
    const height = 380;
    const pad = { left: 58, right: 220, top: 32, bottom: 56 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const xFor = (label) => pad.left + (labels.length <= 1 ? plotW / 2 : (labels.indexOf(label) / (labels.length - 1)) * plotW);
    const yFor = (score) => pad.top + (100 - score) / 100 * plotH;

    const axis = [];
    for (let y = 0; y <= 100; y += 20) {
      const py = yFor(y);
      axis.push(`<line x1="${pad.left}" y1="${py}" x2="${pad.left + plotW}" y2="${py}" stroke="#d9dee8" />`);
      axis.push(`<text x="${pad.left - 10}" y="${py + 4}" text-anchor="end" font-size="12" fill="#667085">${y}</text>`);
    }
    labels.forEach((label) => {
      const x = xFor(label);
      axis.push(`<text x="${x}" y="${height - 20}" text-anchor="middle" font-size="12" fill="#667085">${label}</text>`);
    });

    const lines = [];
    const legend = [];
    themes.forEach((theme, index) => {
      const color = palette[index % palette.length];
      const points = theme.points
        .map((p) => ({ ...p, plot_score: plotScore(p) }))
        .filter((p) => p.plot_score !== null);
      const path = points.map((p, pointIndex) => `${pointIndex === 0 ? "M" : "L"} ${xFor(p.x).toFixed(2)} ${yFor(p.plot_score).toFixed(2)}`).join(" ");
      lines.push(`<path d="${path}" fill="none" stroke="${color}" stroke-width="2.4" />`);
      points.forEach((p) => {
        const stroke = pointStroke(p);
        const title = [
          `${theme.theme} ${p.x}`,
          `mainline_score_v6 ${formatMainlineScore(p.mainline_score_v6 ?? p.default_score)}`,
          `兼容证据分 ${formatScore(p.legacy_evidence_score ?? p.evidence_score)}`,
          `主题分 ${formatScore(p.theme_score)}`,
          `ETF分 ${formatScore(p.etf_score)}`,
          `共振 ${p.triple_confirmation ? "三强共振" : "未共振"}`,
          p.stage,
        ].join(" / ");
        lines.push(
          `<circle cx="${xFor(p.x)}" cy="${yFor(p.plot_score)}" r="${pointRadius(p).toFixed(1)}" fill="${color}" fill-opacity="${pointOpacity(p).toFixed(2)}" stroke="${stroke.color}" stroke-width="${stroke.width}"><title>${title}</title></circle>`
        );
      });
      const ly = pad.top + index * 24;
      legend.push(`<line x1="${pad.left + plotW + 24}" y1="${ly}" x2="${pad.left + plotW + 44}" y2="${ly}" stroke="${color}" stroke-width="3" />`);
      legend.push(`<text x="${pad.left + plotW + 52}" y="${ly + 4}" font-size="12" fill="#172033">${theme.theme}</text>`);
    });
    const qualityLegendY = height - 40;
    const qualityLegendX = pad.left + 8;
    legend.push(`<circle cx="${qualityLegendX}" cy="${qualityLegendY}" r="8" fill="#0f766e" fill-opacity="0.9" stroke="#111827" stroke-width="3" />`);
    legend.push(`<text x="${qualityLegendX + 16}" y="${qualityLegendY + 4}" font-size="12" fill="#475467">大点+粗外圈=证据/主题/ETF共振</text>`);

    container.innerHTML = `
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="主线分数曲线">
        <rect x="0" y="0" width="${width}" height="${height}" fill="#fbfcfd" />
        ${axis.join("")}
        <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${pad.top + plotH}" stroke="#98a2b3" />
        <line x1="${pad.left}" y1="${pad.top + plotH}" x2="${pad.left + plotW}" y2="${pad.top + plotH}" stroke="#98a2b3" />
        ${lines.join("")}
        ${legend.join("")}
      </svg>`;
  }

  document.querySelectorAll("[data-source]").forEach(async (container) => {
    try {
      const response = await fetch(container.dataset.source, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      drawScoreChart(container, await response.json());
    } catch (error) {
      container.innerHTML = `<div class="chart-error">曲线数据读取失败：${error.message}</div>`;
    }
  });
})();
