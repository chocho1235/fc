const app = document.querySelector("[data-football-app]");
const rowsEl = document.querySelector("[data-football-rows]");
const countEl = document.querySelector("[data-football-count]");
const upcomingRowsEl = document.querySelector("[data-upcoming-rows]");
const upcomingCountEl = document.querySelector("[data-upcoming-count]");
const searchEl = document.querySelector("[data-football-search]");
const leagueEl = document.querySelector("[data-football-league]");
const filterButtons = [...document.querySelectorAll("[data-filter]")];
const valueListEl = document.querySelector("[data-value-list]");
const ruleListEl = document.querySelector("[data-rule-list]");
const donutEl = document.querySelector("[data-football-donut]");
const generatedAtEl = document.querySelector("[data-generated-at]");
const refreshStatusEl = document.querySelector("[data-refresh-status]");
const refreshBtn = document.querySelector("[data-refresh-btn]");
const tabButtons = [...document.querySelectorAll("[data-tab]")];
const tabPanels = [...document.querySelectorAll(".football-tab-panel")];

const PASSIVE_MIN_PROB = 0.70;       // minimum calibrated per-leg probability to include
const PASSIVE_TARGET_ODDS = 2.0;     // target combined fair odds for the stack
const REFRESH_INTERVAL_MS = 60_000;

let matches = [];
let upcomingMatches = [];
let currentFilter = "all";
let currentLeague = "all";
let lastFetchedAt = null;
let refreshTimer = null;
let currentData = null;

const labelMap = { H: "Home", D: "Draw", A: "Away" };
const resultMap = { H: "Home won", D: "Draw", A: "Away won" };

// ─── Formatting helpers ────────────────────────────────────────────────────────

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function decimal(value) {
  const n = Number(value || 0);
  return n > 0 ? n.toFixed(2) : "--";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"})[c]);
}

function formatGeneratedAt(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function timeAgo(date) {
  if (!date) return "";
  const secs = Math.round((Date.now() - date.getTime()) / 1000);
  if (secs < 10) return "just now";
  if (secs < 60) return `${secs}s ago`;
  return `${Math.floor(secs / 60)}m ago`;
}

function summaryKey(key) {
  return key.toLowerCase().replaceAll(" ", "_").replaceAll("-", "_");
}

// ─── Probability / odds helpers ───────────────────────────────────────────────

function probabilityFor(row, label) {
  if (label === "H") return Number(row.home_win_probability || 0);
  if (label === "D") return Number(row.draw_probability || 0);
  return Number(row.away_win_probability || 0);
}

function bookmakerOddsFor(row, label) {
  if (label === "H") return Number(row.home_bookmaker_odds || 0);
  if (label === "D") return Number(row.draw_bookmaker_odds || 0);
  return Number(row.away_bookmaker_odds || 0);
}

function fairOddsFor(row, label) {
  if (label === "H") return Number(row.home_fair_odds || 0);
  if (label === "D") return Number(row.draw_fair_odds || 0);
  return Number(row.away_fair_odds || 0);
}

function valueOddsFor(row, label) {
  if (label === "H") return Number(row.home_value_odds || 0);
  if (label === "D") return Number(row.draw_value_odds || 0);
  return Number(row.away_value_odds || 0);
}

// ─── Context helpers ──────────────────────────────────────────────────────────

function extractContextNumber(text, pattern) {
  const match = String(text || "").match(pattern);
  return match ? Number(match[1]) : 0;
}

function contextDetails(row) {
  const t = row.context_summary || "";
  const hasAvailabilityContext = /Key absences|Injuries/i.test(t);
  return {
    homeImportance: Number(row.home_auto_importance || 0) + Number(row.home_motivation || 0),
    awayImportance: Number(row.away_auto_importance || 0) + Number(row.away_motivation || 0),
    homeKeyAbsences: Number(row.home_key_absences || extractContextNumber(t, /Key absences H([\d.]+)/i)),
    awayKeyAbsences: Number(row.away_key_absences || extractContextNumber(t, /Key absences H[\d.]+-A([\d.]+)/i)),
    homeInjuries: Number(row.home_injury_count || extractContextNumber(t, /Injuries H([\d.]+)/i)),
    awayInjuries: Number(row.away_injury_count || extractContextNumber(t, /Injuries H[\d.]+-A([\d.]+)/i)),
    homeLineupStrength: Number(row.home_lineup_strength || 1),
    awayLineupStrength: Number(row.away_lineup_strength || 1),
    hasAvailabilityContext,
  };
}

function importanceLabel(v) {
  return v >= 1.4 ? "high" : v >= 0.9 ? "normal" : "low";
}

function lineupLabel(v) {
  if (v >= 0.96) return "strong";
  if (v >= 0.9) return "slightly weakened";
  if (v >= 0.82) return "weakened";
  return "heavily weakened";
}

function latestBracketValue(text, marker) {
  const regex = new RegExp(`${marker}\\[([^\\]]*)\\]`, "g");
  let match, value = "";
  while ((match = regex.exec(String(text || "")))) value = match[1] || "";
  return value;
}

function cleanPlayerToken(token) {
  return token.replace(/\s+[a-z]\s+\d+$/i, "").replace(/\s+\d+$/i, "").trim();
}

function unavailablePlayers(row, side) {
  const raw = latestBracketValue(row.context_notes, side === "home" ? "H" : "A");
  if (!raw || raw.toLowerCase() === "none") return [];
  return raw.split(";").map(cleanPlayerToken).filter(Boolean).slice(0, 6);
}

function contextImpactText(row) {
  const d = contextDetails(row);
  const keyGap = d.awayKeyAbsences - d.homeKeyAbsences;
  const injGap = d.awayInjuries - d.homeInjuries;
  const impGap = d.homeImportance - d.awayImportance;
  if (d.hasAvailabilityContext && Math.abs(keyGap) >= 2) return keyGap > 0 ? "Away missing more key players" : "Home missing more key players";
  if (d.hasAvailabilityContext && Math.abs(injGap) >= 5) return injGap > 0 ? "Away squad looks thinner" : "Home squad looks thinner";
  if (Math.abs(impGap) >= 1) return impGap > 0 ? "Home motivation edge" : "Away motivation edge";
  return "No major context swing";
}

// ─── Rendering helpers ────────────────────────────────────────────────────────

function probabilityBar(row) {
  const home = percent(row.home_win_probability);
  const draw = percent(row.draw_probability);
  const away = percent(row.away_win_probability);
  return `<div class="football-probs" aria-label="Home ${home}, draw ${draw}, away ${away}">
    <span class="football-probs__home" style="width:${home}"></span>
    <span class="football-probs__draw" style="width:${draw}"></span>
    <span class="football-probs__away" style="width:${away}"></span>
  </div>`;
}

function resultHtml(row, includeResult) {
  if (!includeResult || !row.result) return `<span class="football-status football-status--pending">Upcoming</span>`;
  const correct = row.predicted_result === row.result;
  const betWon = row.suggested_bet && row.suggested_bet === row.result;
  const betLost = row.suggested_bet && row.suggested_bet !== row.result;
  const cls = betWon || correct ? "football-status--won" : "football-status--lost";
  return `<div class="football-result">
    <span class="football-status ${cls}">${resultMap[row.result] || row.result}</span>
    <small>${correct ? "Model right" : "Model missed"} · ${betWon ? "Bet won" : betLost ? "Bet lost" : "No bet"}</small>
  </div>`;
}

function builderStatus(row) {
  const won = Number(row.builder_legs_won || 0), total = Number(row.builder_legs_total || 0);
  const r = row.builder_result || "";
  if (r === "won") return { label: `Builder won · ${won}/${total}`, className: "football-status--won" };
  if (r === "partial") return { label: `Builder partial · ${won}/${total}`, className: "football-status--partial" };
  if (r === "lost") return { label: `Builder lost · ${won}/${total}`, className: "football-status--lost" };
  return { label: "Not settled", className: "football-status--pending" };
}

function builderLegResultHtml(row) {
  const parts = String(row.builder_leg_results || "").split(" | ").map(p => p.trim()).filter(Boolean);
  if (!parts.length) return "";
  return `<ul class="football-builder-results">${parts.map(p => {
    const [label, status = "pending"] = p.split(":");
    return `<li class="is-${escapeHtml(status)}"><span>${escapeHtml(label)}</span><b>${escapeHtml(status)}</b></li>`;
  }).join("")}</ul>`;
}

function h2hHtml(row) {
  const homeForm = row.h2h_home_form || "No recent H2H";
  const awayForm = row.h2h_away_form || "No recent H2H";
  if (homeForm === "No recent H2H" && awayForm === "No recent H2H") return `<span class="football-h2h-empty">No recent H2H</span>`;
  const chips = (form) => form.split(" ").filter(Boolean).map(r => `<i class="football-h2h__${r.toLowerCase()}">${escapeHtml(r)}</i>`).join("");
  return `<div class="football-h2h" aria-label="Previous head-to-head form">
    <span>H2H</span>
    <b>${escapeHtml(row.home_team)}</b><em>${chips(homeForm)}</em>
    <b>${escapeHtml(row.away_team)}</b><em>${chips(awayForm)}</em>
  </div>`;
}

function oddsCard(row) {
  return `<div class="football-odds-grid">${["H","D","A"].map(label => `
    <div class="football-odds-cell${row.predicted_result === label ? " is-pick" : ""}">
      <span>${labelMap[label]}</span>
      <strong>${decimal(bookmakerOddsFor(row, label))}</strong>
      <small>Bet from ${decimal(valueOddsFor(row, label))}</small>
    </div>`).join("")}</div>`;
}

function decisionHtml(row) {
  const label = row.suggested_bet || row.predicted_result;
  const isValue = Boolean(row.suggested_bet);
  return `<div class="football-decision ${isValue ? "football-decision--bet" : ""}">
    <span class="football-decision__label">${isValue ? `Bet ${labelMap[label]}` : "No 1X2 bet"}</span>
    <strong>${labelMap[label]} ${percent(probabilityFor(row, label))}</strong>
    <span>Bookie ${decimal(bookmakerOddsFor(row, label))} · fair ${decimal(fairOddsFor(row, label))}</span>
    <b>${isValue ? `Edge +${percent(row.suggested_edge)}` : `Only bet if ${labelMap[label]} reaches ${decimal(valueOddsFor(row, label))}`}</b>
    ${isValue && row.bet_rule ? `<small>Qualified by ${escapeHtml(row.bet_rule)} · ${row.bet_rule_bets} bets · ${percent(row.bet_rule_roi)} ROI</small>` : ""}
  </div>`;
}

function builderHtml(row) {
  const probability = Number(row.over_25_probability || 0);
  const bookOdds = Number(row.over_25_bookmaker_odds || 0);
  const valueOdds = Number(row.over_25_value_odds || 0);
  const hasValue = bookOdds > 0 && valueOdds > 0 && bookOdds >= valueOdds;
  const suggestion = row.builder_suggestion || "No builder lean";
  const confidence = row.builder_confidence || "none";
  const legs = suggestion === "No builder lean" ? [] : suggestion.split(" + ").filter(Boolean);
  const confidenceText = confidence === "strong" ? "Strong lean" : confidence === "lean" ? "Lean only" : "No clear combo";
  const settled = builderStatus(row);
  return `<div class="football-builder football-builder--${escapeHtml(confidence)} ${hasValue ? "football-builder--value" : ""}">
    <div class="football-builder__head"><span>Builder projection</span><b>${escapeHtml(confidenceText)}</b></div>
    <span class="football-status ${settled.className}">${escapeHtml(settled.label)}</span>
    <div class="football-builder__legs">${legs.length ? legs.map(leg => `<i>${escapeHtml(leg)}</i>`).join("") : "<em>No combined lean</em>"}</div>
    ${builderLegResultHtml(row)}
    <dl class="football-builder__metrics">
      <div><dt>O2.5</dt><dd>${percent(probability)}</dd></div>
      <div><dt>BTTS</dt><dd>${percent(Number(row.btts_probability || 0))}</dd></div>
      <div><dt>SOT</dt><dd>${decimal(Number(row.total_expected_sot || 0))}</dd></div>
    </dl>
    <small>${escapeHtml(row.home_team)} SOT ${decimal(Number(row.home_expected_sot||0))} · ${escapeHtml(row.away_team)} SOT ${decimal(Number(row.away_expected_sot||0))}</small>
    <small>Corners ${decimal(Number(row.total_expected_corners||0))} · cards ${decimal(Number(row.total_expected_cards||0))} · O1.5 ${percent(Number(row.over_15_probability||0))} · O3.5 ${percent(Number(row.over_35_probability||0))}</small>
    <small>${bookOdds ? `O2.5 odds ${decimal(bookOdds)} · value from ${decimal(valueOdds)}` : "Stat legs need bookmaker lines before staking"}</small>
  </div>`;
}

function contextHtml(row) {
  const d = contextDetails(row);
  const homePlayers = unavailablePlayers(row, "home");
  const awayPlayers = unavailablePlayers(row, "away");
  const avail = (key, inj) => d.hasAvailabilityContext ? `${inj} unavailable · key impact ${decimal(key)}` : "Availability not loaded";
  const playerList = (players) => players.length ? `<ul>${players.map(n => `<li>${escapeHtml(n)}</li>`).join("")}</ul>` : "";
  return `<div class="football-context-card">
    <strong>${escapeHtml(contextImpactText(row))}</strong>
    <div>
      <span>${escapeHtml(row.home_team)}</span>
      <b>${avail(d.homeKeyAbsences, d.homeInjuries)}</b>
      <small>Lineup strength ${percent(d.homeLineupStrength)} (${lineupLabel(d.homeLineupStrength)})</small>
      <small>Importance ${d.homeImportance.toFixed(1)} (${importanceLabel(d.homeImportance)})</small>
      ${playerList(homePlayers)}
    </div>
    <div>
      <span>${escapeHtml(row.away_team)}</span>
      <b>${avail(d.awayKeyAbsences, d.awayInjuries)}</b>
      <small>Lineup strength ${percent(d.awayLineupStrength)} (${lineupLabel(d.awayLineupStrength)})</small>
      <small>Importance ${d.awayImportance.toFixed(1)} (${importanceLabel(d.awayImportance)})</small>
      ${playerList(awayPlayers)}
    </div>
  </div>`;
}

function matchRowHtml(row, includeResult = true) {
  const parts = [row.date];
  if (row.league) parts.unshift(row.league);
  if (row.time) parts.push(row.time);
  return `<tr class="football-match-row ${row.suggested_bet ? "football-match-row--value" : ""}">
    <td colspan="4">
      <article class="football-match-card">
        <div class="football-match-card__summary">
          <div class="football-fixture-card">
            <div>
              <strong>${escapeHtml(row.home_team)} v ${escapeHtml(row.away_team)}</strong>
              <span>${escapeHtml(parts.join(" · "))}</span>
            </div>
            ${resultHtml(row, includeResult)}
          </div>
          ${decisionHtml(row)}
          ${builderHtml(row)}
          <details class="football-expand">
            <summary>Details</summary>
            <div class="football-expand__grid">
              <section><h3>1X2 prices</h3>${oddsCard(row)}</section>
              <section><h3>Context</h3>${contextHtml(row)}</section>
              <section><h3>Head to head</h3>${h2hHtml(row)}</section>
              <section><h3>Probabilities</h3>${probabilityBar(row)}</section>
            </div>
          </details>
        </div>
      </article>
    </td>
  </tr>`;
}

// ─── Passive betting ──────────────────────────────────────────────────────────

// Poisson probability: P(X >= k) for a Poisson(lambda) random variable
function poissonAtLeast(k, lambda) {
  if (lambda <= 0) return k === 0 ? 1 : 0;
  let cumulative = 0;
  let term = Math.exp(-lambda);
  for (let i = 0; i < k; i++) {
    cumulative += term;
    term *= lambda / (i + 1);
  }
  return 1 - cumulative;
}

// All possible passive outcomes for a fixture, derived from model + Poisson.
// Focuses on the 80–93% sweet spot: high enough to be reliable, low enough
// that fair odds contribute meaningfully to a 2× accumulator stack.
function allOutcomesForFixture(row) {
  const hg  = Number(row.expected_home_goals    || 0);
  const ag  = Number(row.expected_away_goals    || 0);
  const tg  = Number(row.expected_total_goals   || 0);
  const hs  = Number(row.home_expected_sot      || 0);
  const as_ = Number(row.away_expected_sot      || 0);
  const co  = Number(row.total_expected_corners || 0);
  const home = row.home_team, away = row.away_team;

  const out = [];

  // Goals markets — model-predicted or Poisson from expected goals
  if (tg > 0) {
    out.push({ label: "Over 1.5 goals", type: "Goals",
               prob: Number(row.over_15_probability) || poissonAtLeast(2, tg), key: "over_15_goals" });
  }
  // Team-to-score: useful when expected goals ≥ 1.3 → P(score) ≥ ~73%
  if (hg >= 1.0) out.push({ label: `${home} to score`, type: "Goals", prob: poissonAtLeast(1, hg), key: "home_to_score" });
  if (ag >= 1.0) out.push({ label: `${away} to score`, type: "Goals", prob: poissonAtLeast(1, ag), key: "away_to_score" });

  // SOT markets — use 3+ or 4+ depending on expected SOT to stay in the sweet spot
  // 3+ SOT: useful when expected SOT ≈ 3–5 → P ≈ 80–94%
  // 4+ SOT: useful when expected SOT ≈ 4–7 → P ≈ 77–91%
  if (hs > 0) {
    const p3 = poissonAtLeast(3, hs), p4 = poissonAtLeast(4, hs);
    if (p4 >= 0.78 && p4 <= 0.94)      out.push({ label: `${home} 4+ shots on target`, type: "SOT", prob: p4, key: "home_4sot" });
    else if (p3 >= 0.78 && p3 <= 0.94) out.push({ label: `${home} 3+ shots on target`, type: "SOT", prob: p3, key: "home_3sot" });
  }
  if (as_ > 0) {
    const p3 = poissonAtLeast(3, as_), p4 = poissonAtLeast(4, as_);
    if (p4 >= 0.78 && p4 <= 0.94)      out.push({ label: `${away} 4+ shots on target`, type: "SOT", prob: p4, key: "away_4sot" });
    else if (p3 >= 0.78 && p3 <= 0.94) out.push({ label: `${away} 3+ shots on target`, type: "SOT", prob: p3, key: "away_3sot" });
  }

  // Corners: 8+ or 10+ depending on expected corners
  if (co > 0) {
    const p8  = poissonAtLeast(8,  co);
    const p10 = poissonAtLeast(10, co);
    if (p8 >= 0.78 && p8 <= 0.94)       out.push({ label: "Match 8+ corners",  type: "Corners", prob: p8,  key: "corners_8" });
    else if (p10 >= 0.78 && p10 <= 0.94) out.push({ label: "Match 10+ corners", type: "Corners", prob: p10, key: "corners_10" });
  }

  // 1X2 result — only include if genuinely strong favourite
  const hw = Number(row.home_win_probability || 0);
  const aw = Number(row.away_win_probability || 0);
  if (hw > 0) out.push({ label: `${home} to win`, type: "Result", prob: hw, key: "home_win" });
  if (aw > 0) out.push({ label: `${away} to win`, type: "Result", prob: aw, key: "away_win" });

  // BTTS
  const btts = Number(row.btts_probability || 0);
  if (btts > 0) out.push({ label: "Both teams to score", type: "Goals", prob: btts, key: "btts_yes" });

  return out.filter(o => o.prob > 0 && o.prob < 1);
}

function computePassivePicks(upcoming, builderProfile) {
  const profileLegs = builderProfile?.legs || {};

  // Map outcome keys to builder_profile leg keys
  const KEY_MAP = {
    home_4sot: "home_4_sot",
    away_4sot: "away_4_sot",
    home_3sot: "home_3_sot",
    corners_8: "total_8_corners",
    corners_10: "total_10_corners",
    btts_yes: "btts_yes",
    over_15_goals: "over_15_goals",
  };

  // Use historical hit rate when well-established (20+ settled), else blend with Poisson.
  // This fixes the calibration problem: Poisson over-estimates probabilities,
  // e.g. 8+ corners gets Poisson 92% but actual hit rate is 75%.
  function calibrateProb(key, rawProb) {
    const profileKey = KEY_MAP[key] || key;
    const leg = profileLegs[profileKey];
    if (!leg || !leg.settled) return rawProb;
    const hitRate = leg.won / leg.settled;
    if (leg.settled >= 20) return hitRate;                      // pure historical
    return hitRate * 0.7 + rawProb * 0.3;                      // blended
  }

  const candidates = [];

  for (const row of upcoming) {
    const outcomes = allOutcomesForFixture(row);

    const enriched = outcomes.map(o => {
      const calProb = calibrateProb(o.key, o.prob);
      return { ...o, rawProb: o.prob, prob: calProb, fairOdds: calProb > 0 ? 1 / calProb : 0 };
    });

    const valid = enriched
      .filter(o => o.rawProb >= 0.72 && o.prob >= PASSIVE_MIN_PROB)
      .sort((a, b) => b.prob - a.prob);
    if (!valid.length) continue;

    const best = valid[0];
    candidates.push({
      fixture: `${row.home_team} v ${row.away_team}`,
      league: row.league || "",
      date: row.date || "",
      time: row.time || "",
      label: best.label,
      type: best.type,
      prob: best.prob,
      rawProb: best.rawProb,
      fairOdds: best.fairOdds,
      allValid: valid,
      row,
    });
  }

  candidates.sort((a, b) => b.prob - a.prob);
  return candidates;
}

// Build a stack that targets PASSIVE_TARGET_ODDS combined fair odds
function buildPassiveStack(picks) {
  const stack = [];
  let combinedOdds = 1;
  for (const pick of picks) {
    if (combinedOdds >= PASSIVE_TARGET_ODDS) break;
    stack.push(pick);
    combinedOdds *= pick.fairOdds;
    if (stack.length >= 8) break; // cap at 8 legs
  }
  return stack;
}

function computeHistoricalPassiveAccuracy(latestMatches) {
  const results = [];
  for (const row of latestMatches) {
    if (!row.result) continue;
    const candidates = [
      { prob: Number(row.home_win_probability || 0), sel: "H" },
      { prob: Number(row.away_win_probability || 0), sel: "A" },
      { prob: Number(row.draw_probability || 0), sel: "D" },
    ].filter(c => c.prob >= PASSIVE_MIN_PROB).sort((a, b) => b.prob - a.prob);

    if (!candidates.length) continue;
    results.push({ hit: candidates[0].sel === row.result, prob: candidates[0].prob });
  }

  const settled = results.length;
  const hits = results.filter(r => r.hit).length;
  return { settled, hits, hitRate: settled ? hits / settled : 0 };
}

// ─── Virtual bank interactive chart ──────────────────────────────────────────

const _vbCharts = new Map(); // chartId → { pnl, zoom, panOffset, container }

const VB_W = 260, VB_H = 70;

function _vbRender(chartId) {
  const state = _vbCharts.get(chartId);
  if (!state) return;
  const { pnl, zoom, panOffset, container } = state;
  const series = pnl.series || [];
  if (series.length < 2) return;

  // series entries: [balance, date, match, won]  (from Python) or plain numbers (legacy)
  const values = series.map(p => (Array.isArray(p) ? p[0] : p));
  const dates  = series.map(p => (Array.isArray(p) ? p[1] : ""));
  const labels = series.map(p => (Array.isArray(p) ? p[2] : ""));
  const won    = series.map(p => (Array.isArray(p) ? p[3] : null));

  // Windowing for zoom + pan
  const total = values.length;
  const visibleCount = Math.max(4, Math.round(total / zoom));
  const maxPan = total - visibleCount;
  const startIdx = Math.max(0, Math.min(maxPan, panOffset));
  const slice = values.slice(startIdx, startIdx + visibleCount);
  const sliceDates = dates.slice(startIdx, startIdx + visibleCount);
  const sliceLabels = labels.slice(startIdx, startIdx + visibleCount);
  const sliceWon = won.slice(startIdx, startIdx + visibleCount);

  const minVal = Math.min(...slice, pnl.starting_bank);
  const maxVal = Math.max(...slice, pnl.starting_bank);
  const range = maxVal - minVal || 1;
  const pad = 6;

  const toX = (i) => (i / (slice.length - 1)) * VB_W;
  const toY = (v) => VB_H - pad - ((v - minVal) / range) * (VB_H - pad * 2);

  // Baseline (starting bank)
  const baseY = toY(pnl.starting_bank).toFixed(1);

  // Polyline points
  const pts = slice.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(" ");
  const positive = slice[slice.length - 1] >= pnl.starting_bank;
  const lineColor = positive ? "#16a34a" : "#dc2626";
  const fillColor = positive ? "rgba(22,163,74,0.08)" : "rgba(220,38,38,0.08)";

  // Filled area path
  const fillPath = `M ${toX(0).toFixed(1)},${toY(slice[0]).toFixed(1)} `
    + slice.map((v, i) => `L ${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(" ")
    + ` L ${toX(slice.length - 1).toFixed(1)},${VB_H} L 0,${VB_H} Z`;

  // Dot markers for wins/losses
  const dots = sliceWon.map((w, i) => {
    if (w === null) return "";
    const cx = toX(i).toFixed(1), cy = toY(slice[i]).toFixed(1);
    const fill = w ? "#16a34a" : "#dc2626";
    const tip = `${sliceLabels[i] || ""} · ${sliceDates[i] || ""} · £${slice[i].toFixed(2)}`;
    return `<circle cx="${cx}" cy="${cy}" r="2.5" fill="${fill}" opacity="0.7" data-tip="${escapeHtml(tip)}"/>`;
  }).join("");

  // Zoom label
  const zoomLabel = zoom <= 1 ? "All" : `Last ${Math.round(100 / zoom)}%`;

  const svgId = `vb-svg-${chartId}`;
  container.innerHTML = `
    <div class="vb-chart-wrap" data-chart-id="${escapeHtml(chartId)}">
      <div class="vb-toolbar">
        <button class="vb-btn" data-vb-zoom-in title="Zoom in">+</button>
        <button class="vb-btn" data-vb-zoom-out title="Zoom out">−</button>
        <span class="vb-zoom-label">${zoomLabel}</span>
        <button class="vb-btn vb-btn--right" data-vb-pan="-1" title="Earlier">‹</button>
        <button class="vb-btn vb-btn--right" data-vb-pan="1" title="Later">›</button>
      </div>
      <svg id="${svgId}" viewBox="0 0 ${VB_W} ${VB_H}" width="100%" height="${VB_H}"
           class="vb-svg" role="img" aria-label="Bank balance over time">
        <path d="${fillPath}" fill="${fillColor}"/>
        <line x1="0" y1="${baseY}" x2="${VB_W}" y2="${baseY}"
              stroke="#94a3b8" stroke-width="0.8" stroke-dasharray="3 2"/>
        <polyline points="${pts}" fill="none" stroke="${lineColor}" stroke-width="1.8"
                  stroke-linejoin="round" stroke-linecap="round"/>
        ${dots}
      </svg>
      <div class="vb-tooltip" id="vb-tip-${chartId}" hidden></div>
    </div>`;

  // Wire up buttons
  const wrap = container.querySelector(".vb-chart-wrap");
  wrap.querySelector("[data-vb-zoom-in]").addEventListener("click", () => _vbZoom(chartId, 2));
  wrap.querySelector("[data-vb-zoom-out]").addEventListener("click", () => _vbZoom(chartId, 0.5));
  wrap.querySelector("[data-vb-pan='-1']").addEventListener("click", () => _vbPan(chartId, -1));
  wrap.querySelector("[data-vb-pan='1']").addEventListener("click", () => _vbPan(chartId, 1));

  // Tooltip on dot hover
  const svg = container.querySelector(`#${svgId}`);
  const tip = container.querySelector(`#vb-tip-${chartId}`);
  svg.addEventListener("mousemove", (e) => {
    const target = e.target.closest("circle[data-tip]");
    if (!target) { tip.hidden = true; return; }
    tip.textContent = target.dataset.tip;
    tip.hidden = false;
  });
  svg.addEventListener("mouseleave", () => { tip.hidden = true; });
}

function _vbZoom(chartId, factor) {
  const state = _vbCharts.get(chartId);
  if (!state) return;
  const newZoom = Math.max(1, Math.min(32, state.zoom * factor));
  state.zoom = newZoom;
  _vbRender(chartId);
}

function _vbPan(chartId, dir) {
  const state = _vbCharts.get(chartId);
  if (!state) return;
  const total = (state.pnl.series || []).length;
  const visibleCount = Math.max(4, Math.round(total / state.zoom));
  const step = Math.max(1, Math.round(visibleCount * 0.4));
  state.panOffset = Math.max(0, state.panOffset + dir * step);
  _vbRender(chartId);
}

function virtualBankHtml(pnl, chartId) {
  const profit = pnl.current_bank - pnl.starting_bank;
  const positive = profit >= 0;
  const sign = positive ? "+" : "";
  const id = chartId || `vb-${Math.random().toString(36).slice(2, 7)}`;
  // Register or update chart state
  const existing = _vbCharts.get(id);
  _vbCharts.set(id, { pnl, zoom: existing?.zoom ?? 1, panOffset: existing?.panOffset ?? 0, container: null });
  return `<div class="virtual-bank ${positive ? "is-positive" : "is-negative"}" data-vb-id="${id}">
    <div class="virtual-bank__head">
      <span>Virtual bank</span>
      <strong>£${pnl.current_bank.toFixed(2)}</strong>
    </div>
    <div class="virtual-bank__kpis">
      <div><span>P&amp;L</span><b class="${positive ? "is-positive" : "is-negative"}">${sign}£${profit.toFixed(2)}</b></div>
      <div><span>ROI</span><b class="${positive ? "is-positive" : "is-negative"}">${sign}${(pnl.roi * 100).toFixed(1)}%</b></div>
      <div><span>Bets</span><b>${pnl.bets}</b></div>
      <div><span>Win rate</span><b>${percent(pnl.win_rate)}</b></div>
    </div>
    <div class="vb-chart-container" data-vb-chart="${id}"></div>
    <div class="virtual-bank__method">£10 flat stake · win = Pinnacle odds × stake − stake · loss = −£10 · each dot = one bet</div>
  </div>`;
}

function _vbAttach(root) {
  // After virtualBankHtml is injected into DOM, attach chart renderers
  root.querySelectorAll("[data-vb-chart]").forEach(el => {
    const id = el.dataset.vbChart;
    const state = _vbCharts.get(id);
    if (!state) return;
    state.container = el;
    _vbRender(id);
  });
}

function renderVirtualBank(el, pnl, chartId) {
  if (!el || !pnl) return;
  el.innerHTML = virtualBankHtml(pnl, chartId);
  _vbAttach(el);
}

function computePassivePnl(latestMatches) {
  const STAKE = 10;
  const STARTING_BANK = 1000;
  let balance = STARTING_BANK;
  let bets = 0, wins = 0;
  const series = [];
  for (const row of (latestMatches || [])) {
    if (!row.result) continue;
    const candidates = [
      { prob: Number(row.home_win_probability || 0), sel: "H" },
      { prob: Number(row.away_win_probability || 0), sel: "A" },
      { prob: Number(row.draw_probability || 0), sel: "D" },
    ].filter(c => c.prob >= 0.65).sort((a, b) => b.prob - a.prob);
    if (!candidates.length) continue;
    const best = candidates[0];
    const fairOdds = 1 / best.prob;
    bets++;
    const won = best.sel === row.result;
    if (won) { balance += STAKE * (fairOdds - 1); wins++; }
    else      { balance -= STAKE; }
    series.push([Math.round(balance * 100) / 100, row.date || "", `${row.home_team} v ${row.away_team}`, won]);
  }
  return {
    starting_bank: STARTING_BANK,
    current_bank: Math.round(balance * 100) / 100,
    bets, wins,
    win_rate: bets ? wins / bets : 0,
    roi: bets ? (balance - STARTING_BANK) / (bets * STAKE) : 0,
    series,
  };
}

function renderPassive(data) {
  const stackEl = document.querySelector("[data-passive-stack]");
  const stackSummaryEl = document.querySelector("[data-passive-stack-summary]");
  const picksEl = document.querySelector("[data-passive-picks]");
  const picksCountEl = document.querySelector("[data-passive-picks-count]");
  const learningEl = document.querySelector("[data-passive-learning]");
  const historyEl = document.querySelector("[data-passive-history]");
  if (!stackEl) return;

  const picks = computePassivePicks(data.upcoming || [], data.builder_profile || {});
  const accuracy = computeHistoricalPassiveAccuracy(data.latest_matches || []);

  // — Smart stack: build legs until combined fair odds hits 2×
  const stackPicks = buildPassiveStack(picks);
  if (!stackPicks.length) {
    stackEl.innerHTML = `<p class="football-empty">No picks above ${Math.round(PASSIVE_MIN_PROB * 100)}% in upcoming fixtures.</p>`;
    stackSummaryEl.innerHTML = "";
  } else {
    const combinedProb = stackPicks.reduce((p, c) => p * c.prob, 1);
    const combinedFairOdds = stackPicks.reduce((p, c) => p * c.fairOdds, 1);
    const targetMet = combinedFairOdds >= PASSIVE_TARGET_ODDS;
    stackSummaryEl.innerHTML = `
      <div class="passive-stack-kpis">
        <div><span>Combined prob</span><strong>${percent(combinedProb)}</strong></div>
        <div class="${targetMet ? "is-target-met" : ""}"><span>Combined odds</span><strong>${combinedFairOdds.toFixed(2)}×</strong></div>
        <div><span>Legs</span><strong>${stackPicks.length}</strong></div>
        <div><span>Target</span><strong>${PASSIVE_TARGET_ODDS.toFixed(1)}×</strong></div>
      </div>`;
    stackEl.innerHTML = stackPicks.map((pick, i) => {
      const typeClass = { "SOT": "sot", "Corners": "corners", "Goals": "goals", "Result": "result" }[pick.type] || "result";
      return `
      <div class="passive-leg">
        <div class="passive-leg__rank">${i + 1}</div>
        <div class="passive-leg__body">
          <strong>${escapeHtml(pick.fixture)}</strong>
          <span>${escapeHtml([pick.league, pick.date].filter(Boolean).join(" · "))}</span>
        </div>
        <div class="passive-leg__pick">
          <b>${escapeHtml(pick.label)}</b>
          <span class="passive-leg__type passive-leg__type--${typeClass}">${escapeHtml(pick.type)}</span>
        </div>
        <div class="passive-leg__prob">
          <strong>${percent(pick.prob)}</strong>
          <small>fair ${pick.fairOdds.toFixed(2)}</small>
        </div>
      </div>`;
    }).join("");
  }

  // — All picks (best outcome per fixture, sorted by probability)
  picksCountEl.textContent = `${picks.length} picks`;
  if (!picks.length) {
    picksEl.innerHTML = `<p class="football-empty">No upcoming fixtures exceed ${Math.round(PASSIVE_MIN_PROB * 100)}% confidence.</p>`;
  } else {
    picksEl.innerHTML = picks.map(pick => {
      const typeClass = { "SOT": "sot", "Corners": "corners", "Goals": "goals", "Result": "result" }[pick.type] || "result";
      // Show up to 3 top alternatives for this fixture
      const alts = (pick.allValid || []).slice(1, 4)
        .map(o => `<span>${escapeHtml(o.label)} ${percent(o.prob)}</span>`).join("");
      return `
      <div class="passive-pick">
        <div class="passive-pick__fixture">
          <strong>${escapeHtml(pick.fixture)}</strong>
          <span>${escapeHtml([pick.league, pick.date].filter(Boolean).join(" · "))}</span>
          ${alts ? `<div class="passive-pick__alts">${alts}</div>` : ""}
        </div>
        <div class="passive-pick__label">
          <b>${escapeHtml(pick.label)}</b>
          <span class="passive-leg__type passive-leg__type--${typeClass}">${escapeHtml(pick.type)}</span>
        </div>
        <div class="passive-pick__prob">
          <strong>${percent(pick.prob)}</strong>
          <small>${pick.fairOdds.toFixed(2)}</small>
        </div>
      </div>`;
    }).join("");
  }

  // — Learning panel (builder profile leg stats)
  const legs = data.builder_profile?.legs || {};
  const legEntries = Object.entries(legs)
    .map(([key, leg]) => ({ key, ...leg, hitRate: leg.settled ? leg.won / leg.settled : 0 }))
    .filter(e => e.settled >= 5)
    .sort((a, b) => b.hitRate - a.hitRate);

  const legLabels = {
    over_15_goals: "Over 1.5 goals",
    over_25_goals: "Over 2.5 goals",
    btts_yes: "BTTS yes",
    home_4_sot: "Home 4+ SOT",
    away_4_sot: "Away 4+ SOT",
    total_8_corners: "8+ corners",
    total_3_cards: "3+ cards",
  };

  if (!legEntries.length && !accuracy.settled) {
    learningEl.innerHTML = `<p class="football-empty">Not enough settled history yet.</p>`;
  } else {
    const accuracyHtml = accuracy.settled ? `
      <div class="passive-learn-row passive-learn-row--total">
        <span>1X2 high-confidence picks</span>
        <div>
          <strong>${percent(accuracy.hitRate)}</strong>
          <small>${accuracy.hits}/${accuracy.settled} hit</small>
        </div>
      </div>` : "";
    learningEl.innerHTML = accuracyHtml + legEntries.map(e => `
      <div class="passive-learn-row">
        <span>${escapeHtml(legLabels[e.key] || e.key)}</span>
        <div>
          <strong>${percent(e.hitRate)}</strong>
          <small>${e.won}/${e.settled} hit</small>
        </div>
      </div>`).join("");
  }

  // — Passive virtual bank (server-computed from rolling predictions at >= 65% confidence)
  const passivePnlEl = document.querySelector("[data-passive-pnl]");
  if (passivePnlEl) {
    const pnl = data.passive_pnl || computePassivePnl(data.latest_matches || []);
    renderVirtualBank(passivePnlEl, pnl, "vb-passive");
  }

  // — Track record (recent high-probability picks from latest matches)
  const history = (data.latest_matches || [])
    .filter(row => row.result)
    .map(row => {
      const candidates = [
        { prob: Number(row.home_win_probability || 0), sel: "H", label: `${row.home_team} to win` },
        { prob: Number(row.away_win_probability || 0), sel: "A", label: `${row.away_team} to win` },
        { prob: Number(row.draw_probability || 0), sel: "D", label: "Draw" },
      ].filter(c => c.prob >= PASSIVE_MIN_PROB).sort((a, b) => b.prob - a.prob);
      if (!candidates.length) return null;
      const best = candidates[0];
      return { fixture: `${row.home_team} v ${row.away_team}`, date: row.date, label: best.label, prob: best.prob, hit: best.sel === row.result };
    })
    .filter(Boolean)
    .slice(-12)
    .reverse();

  if (!history.length) {
    historyEl.innerHTML = `<p class="football-empty">No settled history to show yet.</p>`;
  } else {
    historyEl.innerHTML = history.map(h => `
      <div class="passive-history-row ${h.hit ? "is-hit" : "is-miss"}">
        <div>
          <strong>${escapeHtml(h.fixture)}</strong>
          <span>${escapeHtml(h.date)} · ${escapeHtml(h.label)} · ${percent(h.prob)}</span>
        </div>
        <span class="football-status ${h.hit ? "football-status--won" : "football-status--lost"}">${h.hit ? "Hit" : "Miss"}</span>
      </div>`).join("");
  }
}

// ─── Fixtures tab renders ─────────────────────────────────────────────────────

function rowMatchesFilter(row) {
  if (currentFilter === "suggested") return Boolean(row.suggested_bet);
  if (currentFilter === "home") return row.predicted_result === "H";
  if (currentFilter === "draw") return row.predicted_result === "D";
  if (currentFilter === "away") return row.predicted_result === "A";
  return true;
}

function rowMatchesSearch(row) {
  const q = searchEl?.value.trim().toLowerCase() || "";
  if (!q) return true;
  return `${row.home_team} ${row.away_team}`.toLowerCase().includes(q);
}

function rowMatchesLeague(row) {
  if (currentLeague === "all") return true;
  return (row.league || row.league_code || "") === currentLeague;
}

function renderRows() {
  const visible = matches.filter(rowMatchesFilter).filter(rowMatchesSearch).filter(rowMatchesLeague).slice().reverse();
  if (countEl) countEl.textContent = `${visible.length} shown`;
  if (rowsEl) rowsEl.innerHTML = visible.map(row => matchRowHtml(row, true)).join("");
}

function renderUpcoming() {
  const visible = upcomingMatches.filter(rowMatchesSearch).filter(rowMatchesLeague);
  if (upcomingCountEl) upcomingCountEl.textContent = `${visible.length} shown`;
  if (!upcomingRowsEl) return;
  if (!visible.length) {
    upcomingRowsEl.innerHTML = `<tr><td colspan="4"><span class="football-empty">No upcoming fixtures in the current feed.</span></td></tr>`;
    return;
  }
  upcomingRowsEl.innerHTML = visible.map(row => matchRowHtml(row, false)).join("");
}

function renderValueList(rows) {
  if (!valueListEl) return;
  const suggested = rows.filter(r => r.suggested_bet).slice(-6).reverse();
  if (!suggested.length) {
    valueListEl.innerHTML = `<p class="football-empty">No value selections in this slice.</p>`;
    return;
  }
  valueListEl.innerHTML = suggested.map(row => `
    <article class="football-value">
      <div>
        <strong>${escapeHtml(row.home_team)} v ${escapeHtml(row.away_team)}</strong>
        <span>${escapeHtml(row.date)} · bookie ${decimal(bookmakerOddsFor(row, row.suggested_bet))} · bet from ${decimal(valueOddsFor(row, row.suggested_bet))}</span>
        ${row.bet_rule ? `<small>${escapeHtml(row.bet_rule)} · ${row.bet_rule_bets} bets · ${percent(row.bet_rule_roi)} ROI</small>` : ""}
      </div>
      <b>${labelMap[row.suggested_bet]} +${percent(row.suggested_edge)}</b>
    </article>`).join("");
}

function renderRuleList(rules, health = []) {
  if (!ruleListEl) return;
  const healthByName = new Map((health || []).map(r => [r.name, r]));
  const active = (rules || []).map(r => ({ ...r, status: "active" }));
  const paused = (health || []).filter(r => r.status === "paused").slice(0, 3);
  const visible = [...active.slice(0, 5), ...paused];
  if (!visible.length) {
    ruleListEl.innerHTML = `<p class="football-empty">No qualified rules yet.</p>`;
    return;
  }
  ruleListEl.innerHTML = visible.map(rule => {
    const name = rule.name || `${rule.league} ${rule.selection} ${rule.odds_band} ${percent(rule.min_edge)}+`;
    const healthRow = healthByName.get(name) || rule;
    const paused = healthRow.status === "paused";
    return `<article class="football-rule ${paused ? "football-rule--paused" : ""}">
      <div>
        <strong>${escapeHtml(rule.league)} · ${escapeHtml(labelMap[rule.selection] || rule.selection)} · ${escapeHtml(rule.odds_band)}</strong>
        <span>Edge ${percent(rule.min_edge)}+ · odds ${decimal(rule.min_odds)}-${decimal(rule.max_odds)}</span>
      </div>
      <b>${paused ? "Paused" : `${percent(rule.roi)} ROI`}</b>
      <small>${paused ? escapeHtml(healthRow.pause_reason) : `${rule.bets} bets · recent ${percent(healthRow.recent_roi)} ROI`}</small>
    </article>`;
  }).join("");
}

function renderAverages(averages) {
  const home = Number(averages.home || 0);
  const draw = Number(averages.draw || 0);
  if (donutEl) {
    donutEl.style.setProperty("--home-deg", `${home * 360}deg`);
    donutEl.style.setProperty("--draw-deg", `${(home + draw) * 360}deg`);
  }
  document.querySelectorAll("[data-average='home']").forEach(n => n.textContent = percent(home));
  document.querySelectorAll("[data-average='draw']").forEach(n => n.textContent = percent(draw));
  document.querySelectorAll("[data-average='away']").forEach(n => n.textContent = percent(Number(averages.away || 0)));
}

function renderLeagueOptions(leagues) {
  if (!leagueEl) return;
  leagueEl.innerHTML = [`<option value="all">All leagues</option>`, ...(leagues || []).filter(Boolean).map(l => `<option value="${escapeHtml(l)}">${escapeHtml(l)}</option>`)].join("");
}

function setSummary(summary) {
  document.querySelectorAll("[data-summary]").forEach(node => {
    const key = summaryKey(node.dataset.summary || "");
    node.textContent = summary[key] || "--";
  });
  if (app) {
    app.dataset.latestRoi = summary.roi || "";
    app.dataset.rollingRoi = summary.rolling_roi || "";
  }
}

function setClosingLineSummary(summary) {
  document.querySelectorAll("[data-closing-line]").forEach(node => {
    node.textContent = summary[node.dataset.closingLine || ""] || "--";
  });
}

function setBuilderSummary(summary, profile) {
  document.querySelectorAll("[data-builder-summary]").forEach(node => {
    const key = node.dataset.builderSummary || "";
    if (key === "active_legs") node.textContent = String((profile.allowed_leg_keys || []).length || "--");
    else if (key.endsWith("_rate")) node.textContent = percent(summary[key]);
    else node.textContent = summary[key] ?? "--";
  });
}

// ─── Tab switching ────────────────────────────────────────────────────────────

function switchTab(tabName) {
  tabButtons.forEach(btn => {
    const active = btn.dataset.tab === tabName;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-selected", String(active));
  });
  tabPanels.forEach(panel => {
    const active = panel.id === `panel-${tabName}`;
    panel.classList.toggle("is-active", active);
    panel.hidden = !active;
  });
  if (tabName === "passive" && currentData) renderPassive(currentData);
}

// ─── Auto-refresh ─────────────────────────────────────────────────────────────

function updateRefreshStatus() {
  if (!refreshStatusEl || !lastFetchedAt) return;
  refreshStatusEl.textContent = `Live · ${timeAgo(lastFetchedAt)}`;
}

function startRefreshTimer() {
  if (refreshTimer) clearInterval(refreshTimer);
  // Update the "X ago" label every 15s
  const clockTimer = setInterval(updateRefreshStatus, 15_000);
  // Full data reload every 60s
  refreshTimer = setTimeout(async () => {
    clearInterval(clockTimer);
    await loadData();
    startRefreshTimer();
  }, REFRESH_INTERVAL_MS);
}

// ─── Data loading ─────────────────────────────────────────────────────────────

async function loadData() {
  if (refreshStatusEl) refreshStatusEl.textContent = "Refreshing…";
  try {
    const response = await fetch("/football-model-data.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`Data request failed: ${response.status}`);
    const data = await response.json();
    currentData = data;
    matches = data.latest_matches || [];
    upcomingMatches = data.upcoming || [];
    lastFetchedAt = new Date();

    renderLeagueOptions(data.leagues || []);
    setSummary(data.summary || {});
    setClosingLineSummary(data.closing_line_summary || {});
    setBuilderSummary(data.builder_summary || {}, data.builder_profile || {});
    renderAverages(data.probability_average || {});
    renderValueList(data.suggested_bets || []);
    renderRuleList(data.betting_rules || [], data.betting_rule_health || []);
    if (generatedAtEl) generatedAtEl.textContent = formatGeneratedAt(data.generated_at);
    renderUpcoming();
    renderRows();

    // Fixtures virtual bank
    const fixturePnlEl = document.querySelector("[data-fixture-pnl]");
    if (fixturePnlEl && data.value_bet_pnl) renderVirtualBank(fixturePnlEl, data.value_bet_pnl, "vb-fixtures");

    // Re-render passive tab if it's active
    const activeTab = tabButtons.find(b => b.classList.contains("is-active"))?.dataset.tab;
    if (activeTab === "passive") renderPassive(data);

    if (app) app.dataset.loaded = "true";
    updateRefreshStatus();
  } catch (error) {
    if (refreshStatusEl) refreshStatusEl.textContent = "Offline";
    if (countEl) countEl.textContent = "Data unavailable";
    if (rowsEl) rowsEl.innerHTML = `<tr><td colspan="4"><span class="football-empty">Run the model export script to generate public/football-model-data.json.</span></td></tr>`;
    console.error(error);
  }
}

// ─── Event listeners ──────────────────────────────────────────────────────────

tabButtons.forEach(btn => btn.addEventListener("click", () => switchTab(btn.dataset.tab)));

filterButtons.forEach(btn => btn.addEventListener("click", () => {
  currentFilter = btn.dataset.filter;
  filterButtons.forEach(b => b.classList.toggle("is-active", b === btn));
  renderRows();
}));

searchEl?.addEventListener("input", () => { renderRows(); renderUpcoming(); });

leagueEl?.addEventListener("change", () => {
  currentLeague = leagueEl.value;
  renderUpcoming();
  renderRows();
});

refreshBtn?.addEventListener("click", async () => {
  if (refreshTimer) clearTimeout(refreshTimer);
  await loadData();
  startRefreshTimer();
});

// ─── Boot ─────────────────────────────────────────────────────────────────────

await loadData();
startRefreshTimer();
