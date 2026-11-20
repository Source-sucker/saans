/* Saans UI logic: renders engine output with textContent only (no innerHTML). */
"use strict";

const state = { lang: "en", strings: null, plan: null, lastProfile: null, lastCity: null };

const $ = (id) => document.getElementById(id);

function t(key) {
  return (state.strings && state.strings[state.lang][key]) ?? key;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function loadStrings() {
  const res = await fetch("/api/strings");
  state.strings = await res.json();
}

function memberRow(m = { label: "", age: "", sensitive: false }) {
  const row = el("div", "member-row");
  const labelIn = el("input"); labelIn.type = "text"; labelIn.maxLength = 40;
  labelIn.placeholder = t("member_label_placeholder"); labelIn.value = m.label;
  labelIn.setAttribute("aria-label", t("member_label"));
  const ageIn = el("input"); ageIn.type = "number"; ageIn.min = 0; ageIn.max = 120;
  ageIn.value = m.age; ageIn.setAttribute("aria-label", t("age"));
  const sensLabel = el("label", "visually-hidden");
  const sens = el("input"); sens.type = "checkbox"; sens.checked = m.sensitive;
  sens.id = "sens-" + Math.random().toString(36).slice(2, 8);
  sensLabel.setAttribute("for", sens.id); sensLabel.textContent = t("sensitive");
  const remove = el("button", "remove", t("remove_member")); remove.type = "button";
  remove.addEventListener("click", () => row.remove());
  row.append(labelIn, ageIn, sens, sensLabel, remove);
  return row;
}

function collectProfile() {
  const members = [];
  for (const row of document.querySelectorAll(".member-row")) {
    const [labelIn, ageIn, sensIn] = row.querySelectorAll("input");
    const label = labelIn.value.trim() || "Member";
    const age = ageIn.value === "" ? null : Number(ageIn.value);
    members.push({ label, age_years: age, sensitive: sensIn.checked });
  }
  return {
    members,
    home: {
      purifier: $("purifier").checked,
      unvented_combustion: $("unvented").checked,
      cooking_fuel: $("cooking-fuel").value || null,
    },
    commute: {
      mode: $("commute-mode").value || null,
      km: Number($("commute-km").value) || 0,
    },
  };
}

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 429) throw new Error("error_rate");
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(res.status === 404 ? "error_city" : "error_generic");
  }
  return res.json();
}

function bandClass(band) {
  return "band-" + band.toLowerCase().replace(/\s+/g, "");
}

const BAND_PATTERNS = {  // icon + text + pattern, never colour alone
  "Good": "○", "Satisfactory": "◔", "Moderate": "◑", "Poor": "◕", "Very Poor": "●", "Severe": "◉",
};

function renderPlan(plan) {
  state.plan = plan;
  $("results").hidden = false;

  const status = $("status-line");
  status.replaceChildren();
  if (plan.sample) status.append(el("p", "notice", t("sample_notice")));
  if (plan.stale) status.append(el("p", "notice", t("stale_notice")));

  const aqiCard = $("aqi-line");
  aqiCard.replaceChildren();
  const value = el("span", "aqi-value", String(plan.aqi));
  const bandChip = el("span", "band-chip " + bandClass(plan.band),
                      (BAND_PATTERNS[plan.band] || "") + " " + plan.band);
  const src = el("span", "hint", plan.source === "Open-Meteo (live)" ? t("live_label") : (plan.source || ""));
  aqiCard.append(value, bandChip, src);
  if (plan.grap_stage) {
    aqiCard.append(el("span", "band-chip", t("grap_label") + " " + plan.grap_stage));
  }
  if (plan.sensitive_shift) {
    aqiCard.append(el("span", "hint", t("effective_band_note")));
  }
  if (plan.best_block) {
    aqiCard.append(el("span", null,
      t("best_block_label") + ": " + plan.best_block.start + "-" + plan.best_block.end +
      " (PM2.5 " + plan.best_block.pm25 + " µg/m³)"));
  }

  const cards = $("member-cards");
  cards.replaceChildren();
  for (const m of plan.members || []) {
    const card = el("div", "member-card");
    card.append(el("h4", null, m.label));
    const ul = el("ul");
    for (const aid of m.actions) ul.append(el("li", null, t(aid)));
    if (!m.actions.length) ul.append(el("li", null, "—"));
    card.append(ul);
    cards.append(card);
  }

  const list = $("action-list");
  list.replaceChildren();
  for (const a of plan.actions) {
    list.append(renderAction(a));
  }

  renderCommunity(plan.community);
  $("results-heading").focus();
}

function renderAction(a) {
  const card = el("article", "action" + (a.id === "co_safety" ? " safety" : ""));
  card.append(el("h3", null, a[state.lang === "hi" ? "hi" : "en"]));

  const why = el("p", "why", t("why_this") + " " + (a.why.rule_id) + ": " + (a.why.trigger));
  card.append(why);

  const impact = el("div", "impact-line");
  if (a.exposure && a.exposure !== "n/a") {
    impact.append(el("span", null, t("exposure_label") + ": " + a.exposure));
  }
  if (a.impact && a.impact.value !== null && a.impact.value !== undefined) {
    const kg = a.impact.value.toFixed(3);
    impact.append(el("span", null, t("co2e_label") + ": " + kg + " kg " + t("per_household")));
  } else if (a.impact && a.impact.note) {
    impact.append(el("span", null, a.impact.note));
  }
  if (impact.childNodes.length) card.append(impact);

  if (a.mask_note) card.append(el("p", "hint", a.mask_note));

  if (a.impact && (a.impact.formula || a.impact.source_name)) {
    const details = el("details");
    const summary = el("summary", null, t("show_math"));
    const mathEl = el("div", "math");
    if (a.impact.formula) mathEl.append(document.createTextNode(a.impact.formula + "\n"));
    if (a.impact.inputs) mathEl.append(document.createTextNode(JSON.stringify(a.impact.inputs) + "\n"));
    if (a.impact.source_name) mathEl.append(document.createTextNode("Source: " + a.impact.source_name +
      " (" + a.impact.year + ") " + (a.impact.source_url || "")));
    details.append(summary, mathEl);
    card.append(details);
  }
  return card;
}

function renderCommunity(c) {
  const card = $("community-card");
  card.hidden = false;
  $("community-message").textContent = c[state.lang === "hi" ? "hi" : "en"];
  updateCommunityMath(c);
}

function updateCommunityMath(c) {
  const n = Number($("households").value) || 2;
  const per = c.per_household_co2e_kg;
  const total = (per * n).toFixed(1);
  $("community-math").textContent =
    t("community_total") + ": " + total + " kg CO2e = " + per.toFixed(3) + " x " + n;
}

async function getPlan() {
  const city = $("city").value.trim();
  if (!city) return;
  const btn = $("get-plan");
  btn.disabled = true; btn.textContent = t("loading");
  $("form-error").hidden = true;
  const profile = collectProfile();
  state.lastCity = city; state.lastProfile = profile;
  try {
    const plan = await api("/api/plan", { city, profile });
    renderPlan(plan);
  } catch (e) {
    const err = $("form-error");
    err.textContent = t(e.message || "error_generic");
    err.hidden = false;
  } finally {
    btn.disabled = false; btn.textContent = t("get_plan");
  }
}

async function ask() {
  const q = $("ask-input").value.trim();
  if (!q || !state.lastCity) return;
  $("ask-answer").textContent = "…";
  try {
    const res = await api("/api/ask", { question: q, city: state.lastCity, profile: state.lastProfile });
    $("ask-answer").textContent = res.answer;
    $("ask-source").textContent = res.ai_used ? t("ask_source_note") : t("ask_template_note");
  } catch (e) {
    $("ask-answer").textContent = t(e.message || "error_generic");
  }
}

function applyStrings() {
  document.documentElement.lang = state.lang === "hi" ? "hi" : "en";
  const s = state.strings[state.lang];
  for (const [id, key] of [["app-title", "app_title"], ["tagline", "tagline"],
      ["city-label", "city_label"], ["city-hint", "city_hint"], ["members-title", "members_title"],
      ["home-title", "home_title"], ["cooking-fuel-label", "cooking_fuel"],
      ["commute-title", "commute_title"], ["commute-mode-label", "commute_mode"],
      ["commute-km-label", "commute_km"], ["get-plan", "get_plan"],
      ["members-cards-title", "members_cards_title"], ["community-title-el", "community_title"],
      ["community-households-label", "community_households"], ["ask-title-el", "ask_title"],
      ["ask-label", "ask_placeholder"], ["footer-note", "footer_note"],
      ["large-text-label", "large_text"], ["ask-input", "ask_placeholder"],
      ["city", "city_placeholder"]]) {
    const node = $(id);
    if (!node) continue;
    if (id === "ask-input" || id === "city") node.placeholder = s[key];
    else node.textContent = s[key];
  }
  $("city").setAttribute("placeholder", s.city_placeholder);
  $("lang-toggle").textContent = s.lang_toggle;
  $("add-member").textContent = s.add_member;
  $("ask-button").textContent = s.ask_button;
  $("copy-message").textContent = s.copy;
  $("print-message").textContent = s.print;
  $("results-heading").textContent = s.members_cards_title ? "Today's plan" : "Today's plan";
  if (state.plan) renderPlan(state.plan);  // re-render with new language
}

function init() {
  loadStrings().then(() => {
    applyStrings();
    $("members").append(memberRow({ label: "Adult", age: 35 }));
  });
  $("add-member").addEventListener("click", () =>
    $("members").append(memberRow()));
  $("plan-form").addEventListener("submit", (e) => { e.preventDefault(); getPlan(); });
  $("lang-toggle").addEventListener("click", () => {
    state.lang = state.lang === "en" ? "hi" : "en";
    applyStrings();
  });
  $("large-text").addEventListener("change", (e) => {
    document.documentElement.classList.toggle("large-text", e.target.checked);
  });
  $("ask-button").addEventListener("click", ask);
  $("households").addEventListener("input", () => state.plan && updateCommunityMath(state.plan.community));
  $("copy-message").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("community-message").textContent);
    $("copy-message").textContent = t("copied");
    setTimeout(() => ($("copy-message").textContent = t("copy")), 1500);
  });
  $("print-message").addEventListener("click", () => window.print());
}

document.addEventListener("DOMContentLoaded", init);
