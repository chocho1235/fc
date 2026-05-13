const app = document.querySelector("[data-football-app]");
const rowsEl = document.querySelector("[data-football-rows]");
const countEl = document.querySelector("[data-football-count]");
const upcomingRowsEl = document.querySelector("[data-upcoming-rows]");
const upcomingCountEl = document.querySelector("[data-upcoming-count]");
const searchEl = document.querySelector("[data-football-search]");
const leagueEl = document.querySelector("[data-football-league]");
const filterButtons = [...document.querySelectorAll("[data-filter]")];
const valueListEl = document.querySelector("[data-value-list]");
const donutEl = document.querySelector("[data-football-donut]");

let matches = [];
let upcomingMatches = [];
let currentFilter = "all";
let currentLeague = "all";

const labelMap = {
  H: "Home",
  D: "Draw",
  A: "Away",
};

const resultMap = {
  H: "Home won",
  D: "Draw",
  A: "Away won",
};

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function decimal(value) {
  const number = Number(value || 0);
  return number > 0 ? number.toFixed(2) : "--";
}

function probabilityFor(row, label) {
  if (label === "H") return Number(row.home_win_probability || 0);
  if (label === "D") return Number(row.draw_probability || 0);
  return Number(row.away_win_probability || 0);
}

function extractContextNumber(text, pattern) {
  const match = String(text || "").match(pattern);
  return match ? Number(match[1]) : 0;
}

function contextDetails(row) {
  const contextText = row.context_summary || "";
  const hasAvailabilityContext = /Key absences|Injuries/i.test(contextText);
  const homeImportance = Number(row.home_auto_importance || 0) + Number(row.home_motivation || 0);
  const awayImportance = Number(row.away_auto_importance || 0) + Number(row.away_motivation || 0);
  const homeKeyAbsences = Number(row.home_key_absences || extractContextNumber(contextText, /Key absences H([\d.]+)/i));
  const awayKeyAbsences = Number(row.away_key_absences || extractContextNumber(contextText, /Key absences H[\d.]+-A([\d.]+)/i));
  const homeInjuries = Number(row.home_injury_count || extractContextNumber(contextText, /Injuries H([\d.]+)/i));
  const awayInjuries = Number(row.away_injury_count || extractContextNumber(contextText, /Injuries H[\d.]+-A([\d.]+)/i));
  const homeLineupStrength = Number(row.home_lineup_strength || 1);
  const awayLineupStrength = Number(row.away_lineup_strength || 1);

  return {
    homeImportance,
    awayImportance,
    homeKeyAbsences,
    awayKeyAbsences,
    homeInjuries,
    awayInjuries,
    homeLineupStrength,
    awayLineupStrength,
    hasAvailabilityContext,
  };
}

function importanceLabel(value) {
  if (value >= 1.4) return "high";
  if (value >= 0.9) return "normal";
  return "low";
}

function lineupLabel(value) {
  if (value >= 0.96) return "strong";
  if (value >= 0.9) return "slightly weakened";
  if (value >= 0.82) return "weakened";
  return "heavily weakened";
}

function latestBracketValue(text, marker) {
  const regex = new RegExp(`${marker}\\[([^\\]]*)\\]`, "g");
  let match;
  let value = "";
  while ((match = regex.exec(String(text || "")))) {
    value = match[1] || "";
  }
  return value;
}

function cleanPlayerToken(token) {
  return token
    .replace(/\s+[a-z]\s+\d+$/i, "")
    .replace(/\s+\d+$/i, "")
    .trim();
}

function unavailablePlayers(row, side) {
  const marker = side === "home" ? "H" : "A";
  const raw = latestBracketValue(row.context_notes, marker);
  if (!raw || raw.toLowerCase() === "none") return [];
  return raw
    .split(";")
    .map(cleanPlayerToken)
    .filter(Boolean)
    .slice(0, 6);
}

function contextImpactText(row) {
  const details = contextDetails(row);
  const keyGap = details.awayKeyAbsences - details.homeKeyAbsences;
  const injuryGap = details.awayInjuries - details.homeInjuries;
  const importanceGap = details.homeImportance - details.awayImportance;

  if (details.hasAvailabilityContext && Math.abs(keyGap) >= 2) {
    return keyGap > 0 ? "Away missing more key players" : "Home missing more key players";
  }
  if (details.hasAvailabilityContext && Math.abs(injuryGap) >= 5) {
    return injuryGap > 0 ? "Away squad looks thinner" : "Home squad looks thinner";
  }
  if (Math.abs(importanceGap) >= 1) {
    return importanceGap > 0 ? "Home motivation edge" : "Away motivation edge";
  }
  return "No major context swing";
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

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);
}

function summaryKey(key) {
  return key.toLowerCase().replaceAll(" ", "_").replaceAll("-", "_");
}

function setSummary(summary) {
  document.querySelectorAll("[data-summary]").forEach((node) => {
    const key = summaryKey(node.dataset.summary || "");
    node.textContent = summary[key] || "--";
  });
}

function probabilityBar(row) {
  const home = percent(row.home_win_probability);
  const draw = percent(row.draw_probability);
  const away = percent(row.away_win_probability);
  return `
    <div class="football-probs" aria-label="Home ${home}, draw ${draw}, away ${away}">
      <span class="football-probs__home" style="width: ${home}"></span>
      <span class="football-probs__draw" style="width: ${draw}"></span>
      <span class="football-probs__away" style="width: ${away}"></span>
    </div>
  `;
}

function resultHtml(row, includeResult) {
  if (!includeResult || !row.result) {
    return `<span class="football-status football-status--pending">Upcoming</span>`;
  }
  const correct = row.predicted_result === row.result;
  const betWon = row.suggested_bet && row.suggested_bet === row.result;
  const betLost = row.suggested_bet && row.suggested_bet !== row.result;
  const statusClass = betWon || correct ? "football-status--won" : "football-status--lost";
  const modelText = correct ? "Model right" : "Model missed";
  const betText = betWon ? "Bet won" : betLost ? "Bet lost" : "No bet";
  return `
    <div class="football-result">
      <span class="football-status ${statusClass}">${resultMap[row.result] || row.result}</span>
      <small>${modelText} · ${betText}</small>
    </div>
  `;
}

function h2hHtml(row) {
  const homeForm = row.h2h_home_form || "No recent H2H";
  const awayForm = row.h2h_away_form || "No recent H2H";
  if (homeForm === "No recent H2H" && awayForm === "No recent H2H") {
    return `<span class="football-h2h-empty">No recent H2H</span>`;
  }
  const chips = (form) => form.split(" ").filter(Boolean).map((result) => (
    `<i class="football-h2h__${result.toLowerCase()}">${escapeHtml(result)}</i>`
  )).join("");
  return `
    <div class="football-h2h" aria-label="Previous head-to-head form">
      <span>H2H</span>
      <b>${escapeHtml(row.home_team)}</b>
      <em>${chips(homeForm)}</em>
      <b>${escapeHtml(row.away_team)}</b>
      <em>${chips(awayForm)}</em>
    </div>
  `;
}

function oddsCard(row) {
  return `
    <div class="football-odds-grid" aria-label="1X2 bookmaker odds and value odds">
      ${["H", "D", "A"].map((label) => `
        <div class="football-odds-cell${row.predicted_result === label ? " is-pick" : ""}">
          <span>${labelMap[label]}</span>
          <strong>${decimal(bookmakerOddsFor(row, label))}</strong>
          <small>Bet from ${decimal(valueOddsFor(row, label))}</small>
        </div>
      `).join("")}
    </div>
  `;
}

function decisionHtml(row) {
  const label = row.suggested_bet || row.predicted_result;
  const isValue = Boolean(row.suggested_bet);
  const bookOdds = bookmakerOddsFor(row, label);
  const fairOdds = fairOddsFor(row, label);
  const valueOdds = valueOddsFor(row, label);
  const probability = probabilityFor(row, label);

  return `
    <div class="football-decision ${isValue ? "football-decision--bet" : ""}">
      <span class="football-decision__label">${isValue ? `Bet ${labelMap[label]}` : "No 1X2 bet"}</span>
      <strong>${labelMap[label]} ${percent(probability)}</strong>
      <span>Bookie ${decimal(bookOdds)} · fair ${decimal(fairOdds)}</span>
      <b>${isValue ? `Edge +${percent(row.suggested_edge)}` : `Only bet if ${labelMap[label]} reaches ${decimal(valueOdds)}`}</b>
    </div>
  `;
}

function builderHtml(row) {
  const probability = Number(row.over_25_probability || 0);
  const bookOdds = Number(row.over_25_bookmaker_odds || 0);
  const valueOdds = Number(row.over_25_value_odds || 0);
  const hasValue = bookOdds > 0 && valueOdds > 0 && bookOdds >= valueOdds;
  return `
    <div class="football-builder ${hasValue ? "football-builder--value" : ""}">
      <span>Over 2.5 goals</span>
      <strong>${percent(probability)}</strong>
      <span>Bookie ${decimal(bookOdds)} · bet from ${decimal(valueOdds)}</span>
      <b>${hasValue ? "Possible value" : "No value"}</b>
    </div>
  `;
}

function contextHtml(row) {
  const details = contextDetails(row);
  const homePlayers = unavailablePlayers(row, "home");
  const awayPlayers = unavailablePlayers(row, "away");
  const availabilityLabel = (keyAbsences, injuries) => (
    details.hasAvailabilityContext
      ? `${injuries} unavailable · key impact ${decimal(keyAbsences)}`
      : "Availability not loaded"
  );
  const lineupStrengthLabel = (value) => `Lineup strength ${percent(value)} (${lineupLabel(value)})`;
  const playerList = (players) => players.length
    ? `<ul>${players.map((name) => `<li>${escapeHtml(name)}</li>`).join("")}</ul>`
    : "";
  return `
    <div class="football-context-card">
      <strong>${escapeHtml(contextImpactText(row))}</strong>
      <div>
        <span>${escapeHtml(row.home_team)}</span>
        <b>${availabilityLabel(details.homeKeyAbsences, details.homeInjuries)}</b>
        <small>${lineupStrengthLabel(details.homeLineupStrength)}</small>
        <small>Importance ${details.homeImportance.toFixed(1)} (${importanceLabel(details.homeImportance)})</small>
        ${playerList(homePlayers)}
      </div>
      <div>
        <span>${escapeHtml(row.away_team)}</span>
        <b>${availabilityLabel(details.awayKeyAbsences, details.awayInjuries)}</b>
        <small>${lineupStrengthLabel(details.awayLineupStrength)}</small>
        <small>Importance ${details.awayImportance.toFixed(1)} (${importanceLabel(details.awayImportance)})</small>
        ${playerList(awayPlayers)}
      </div>
    </div>
  `;
}

function rowMatchesFilter(row) {
  if (currentFilter === "suggested") return Boolean(row.suggested_bet);
  if (currentFilter === "home") return row.predicted_result === "H";
  if (currentFilter === "draw") return row.predicted_result === "D";
  if (currentFilter === "away") return row.predicted_result === "A";
  return true;
}

function rowMatchesSearch(row) {
  const query = searchEl.value.trim().toLowerCase();
  if (!query) return true;
  return `${row.home_team} ${row.away_team}`.toLowerCase().includes(query);
}

function rowMatchesLeague(row) {
  if (currentLeague === "all") return true;
  return (row.league || row.league_code || "") === currentLeague;
}

function matchRowHtml(row, includeResult = true) {
    const detailParts = [row.date];
    if (row.league) detailParts.unshift(row.league);
    if (row.time) detailParts.push(row.time);

    return `
      <tr class="football-match-row ${row.suggested_bet ? "football-match-row--value" : ""}">
        <td>
          <div class="football-fixture-card">
            <div>
              <strong>${escapeHtml(row.home_team)} v ${escapeHtml(row.away_team)}</strong>
              <span>${escapeHtml(detailParts.join(" · "))}</span>
            </div>
            ${resultHtml(row, includeResult)}
            ${h2hHtml(row)}
          </div>
        </td>
        <td>${decisionHtml(row)}</td>
        <td>${oddsCard(row)}</td>
        <td>${builderHtml(row)}</td>
        <td>${contextHtml(row)}</td>
      </tr>
      <tr class="football-prob-row">
        <td colspan="5">${probabilityBar(row)}</td>
      </tr>
    `;
}

function renderRows() {
  const visible = matches.filter(rowMatchesFilter).filter(rowMatchesSearch).filter(rowMatchesLeague).slice().reverse();

  countEl.textContent = `${visible.length} shown`;
  rowsEl.innerHTML = visible.map((row) => matchRowHtml(row, true)).join("");
}

function renderUpcoming() {
  const visible = upcomingMatches.filter(rowMatchesSearch).filter(rowMatchesLeague);
  upcomingCountEl.textContent = `${visible.length} shown`;
  if (!visible.length) {
    upcomingRowsEl.innerHTML = `
      <tr>
        <td colspan="5"><span class="football-empty">No upcoming Premier League fixtures in the current feed.</span></td>
      </tr>
    `;
    return;
  }
  upcomingRowsEl.innerHTML = visible.map((row) => matchRowHtml(row, false)).join("");
}

function renderValueList(rows) {
  const suggested = rows.filter((row) => row.suggested_bet).slice(-6).reverse();
  if (!suggested.length) {
    valueListEl.innerHTML = `<p class="football-empty">No value selections in this slice.</p>`;
    return;
  }

  valueListEl.innerHTML = suggested.map((row) => `
    <article class="football-value">
      <div>
        <strong>${escapeHtml(row.home_team)} v ${escapeHtml(row.away_team)}</strong>
        <span>${escapeHtml(row.date)} · bookie ${decimal(bookmakerOddsFor(row, row.suggested_bet))} · bet from ${decimal(valueOddsFor(row, row.suggested_bet))}</span>
      </div>
      <b>${labelMap[row.suggested_bet]} +${percent(row.suggested_edge)}</b>
    </article>
  `).join("");
}

function renderAverages(averages) {
  const home = Number(averages.home || 0);
  const draw = Number(averages.draw || 0);
  const away = Number(averages.away || 0);
  const homeDeg = home * 360;
  const drawDeg = (home + draw) * 360;

  donutEl.style.setProperty("--home-deg", `${homeDeg}deg`);
  donutEl.style.setProperty("--draw-deg", `${drawDeg}deg`);

  document.querySelectorAll("[data-average='home']").forEach((node) => {
    node.textContent = percent(home);
  });
  document.querySelectorAll("[data-average='draw']").forEach((node) => {
    node.textContent = percent(draw);
  });
  document.querySelectorAll("[data-average='away']").forEach((node) => {
    node.textContent = percent(away);
  });
}

function renderLeagueOptions(leagues) {
  if (!leagueEl) return;
  const safeLeagues = (leagues || []).filter(Boolean);
  leagueEl.innerHTML = [
    `<option value="all">All leagues</option>`,
    ...safeLeagues.map((league) => `<option value="${escapeHtml(league)}">${escapeHtml(league)}</option>`),
  ].join("");
}

async function init() {
  try {
    const response = await fetch("/football-model-data.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`Data request failed: ${response.status}`);
    const data = await response.json();
    matches = data.latest_matches || [];
    upcomingMatches = data.upcoming || [];
    renderLeagueOptions(data.leagues || []);
    setSummary(data.summary || {});
    renderAverages(data.probability_average || {});
    renderValueList(data.suggested_bets || []);
    renderUpcoming();
    renderRows();
    app.dataset.loaded = "true";
  } catch (error) {
    countEl.textContent = "Data unavailable";
    rowsEl.innerHTML = `
      <tr>
        <td colspan="5">
          <span class="football-empty">Run the model export script to generate public/football-model-data.json.</span>
        </td>
      </tr>
    `;
    console.error(error);
  }
}

filterButtons.forEach((button) => {
  button.addEventListener("click", () => {
    currentFilter = button.dataset.filter;
    filterButtons.forEach((item) => item.classList.toggle("is-active", item === button));
    renderRows();
  });
});

searchEl.addEventListener("input", renderRows);
searchEl.addEventListener("input", renderUpcoming);
leagueEl?.addEventListener("change", () => {
  currentLeague = leagueEl.value;
  renderUpcoming();
  renderRows();
});

init();
