// Phase 12 -- "graphical analysis" (the piece flagged missing: every
// dashboard's Area/Risk Analysis tab rendered numbers-in-boxes only, never
// an actual chart). Two dependency-free primitives -- no charting library
// added (Rules.md: no new dependency without asking; no network available
// to install one anyway), consistent with every other view in this app
// being plain DOM/innerHTML with no framework.
//
// renderBarChart reuses the .bar-chart/.bar-row/.bar-track/.bar-fill CSS
// classes network.js already established ad-hoc in Phase 8 for its
// centrality bars -- same look, now a shared, reusable function instead
// of one-off markup. renderPieChart is new (inline SVG, no library).

export function renderBarChart(container, data, { valueFormatter } = {}) {
  const max = Math.max(1, ...data.map((d) => d.value));
  const fmt = valueFormatter || ((v) => v);

  if (data.length === 0) {
    container.innerHTML = `<p class="subtle">No data to chart yet.</p>`;
    return;
  }

  container.innerHTML = `
    <div class="bar-chart">
      ${data
        .map(
          (d) => `
        <div class="bar-row">
          <div class="bar-label">${d.label}</div>
          <div class="bar-track"><div class="bar-fill" style="width:${((d.value / max) * 100).toFixed(1)}%;${
            d.color ? ` background:${d.color};` : ""
          }"></div></div>
          <div class="bar-value">${fmt(d.value)}</div>
        </div>`
        )
        .join("")}
    </div>
  `;
}

/**
 * Inline-SVG pie/donut chart. data: [{ label, value, color }]. Slices are
 * drawn as SVG path arcs computed from each value's share of the total --
 * a single nonzero slice draws as a full <circle> since an SVG arc path
 * can't sweep a full 360 degrees on its own.
 */
export function renderPieChart(container, data, { size = 160 } = {}) {
  const nonZero = data.filter((d) => d.value > 0);
  const total = nonZero.reduce((s, d) => s + d.value, 0);

  if (total === 0) {
    container.innerHTML = `<p class="subtle">No data to chart yet.</p>`;
    return;
  }

  const r = size / 2;
  let angle = -Math.PI / 2; // start at 12 o'clock

  const slices = nonZero
    .map((d) => {
      const fraction = d.value / total;
      if (fraction === 1) {
        return `<circle cx="${r}" cy="${r}" r="${r}" fill="${d.color}"><title>${d.label}: ${d.value}</title></circle>`;
      }
      const startAngle = angle;
      angle += fraction * 2 * Math.PI;
      const endAngle = angle;
      const x1 = (r + r * Math.cos(startAngle)).toFixed(2);
      const y1 = (r + r * Math.sin(startAngle)).toFixed(2);
      const x2 = (r + r * Math.cos(endAngle)).toFixed(2);
      const y2 = (r + r * Math.sin(endAngle)).toFixed(2);
      const largeArc = endAngle - startAngle > Math.PI ? 1 : 0;
      return `<path d="M ${r},${r} L ${x1},${y1} A ${r},${r} 0 ${largeArc} 1 ${x2},${y2} Z" fill="${d.color}"><title>${d.label}: ${d.value}</title></path>`;
    })
    .join("");

  const legend = data
    .map(
      (d) => `
      <div class="chart-legend-item">
        <span class="chart-legend-swatch" style="background:${d.color}"></span>${d.label} (${d.value})
      </div>`
    )
    .join("");

  container.innerHTML = `
    <div class="chart-row">
      <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img">${slices}</svg>
      <div class="chart-legend">${legend}</div>
    </div>
  `;
}
