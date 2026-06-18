(function () {
  const palette = ["#0f766e", "#b42318", "#175cd3", "#7a5af8", "#b54708", "#067647", "#c11574", "#475467"];

  function numberOrNull(value) {
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
  }

  function drawScoreChart(container, payload) {
    const themes = (payload.themes || []).filter((item) => (item.points || []).some((p) => numberOrNull(p.score) !== null));
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
        .map((p) => ({ ...p, score: numberOrNull(p.score) }))
        .filter((p) => p.score !== null);
      const path = points.map((p, pointIndex) => `${pointIndex === 0 ? "M" : "L"} ${xFor(p.x).toFixed(2)} ${yFor(p.score).toFixed(2)}`).join(" ");
      lines.push(`<path d="${path}" fill="none" stroke="${color}" stroke-width="2.4" />`);
      points.forEach((p) => {
        const title = `${theme.theme} ${p.x} 分数 ${p.score.toFixed(2)} ${p.stage}`;
        lines.push(`<circle cx="${xFor(p.x)}" cy="${yFor(p.score)}" r="4" fill="${color}"><title>${title}</title></circle>`);
      });
      const ly = pad.top + index * 24;
      legend.push(`<line x1="${pad.left + plotW + 24}" y1="${ly}" x2="${pad.left + plotW + 44}" y2="${ly}" stroke="${color}" stroke-width="3" />`);
      legend.push(`<text x="${pad.left + plotW + 52}" y="${ly + 4}" font-size="12" fill="#172033">${theme.theme}</text>`);
    });

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
