/**
 * Thread helpers for mental-model build → conversation cards.
 */
import { THREAD_KINDS, makeItem } from "./types";

/** Normalize API build/query payloads to the design object used by cards. */
export function normalizeMentalModelContent(raw) {
  if (!raw) return null;
  const revision =
    raw.mental_model && typeof raw.mental_model === "object" ? raw.mental_model : raw;
  const content =
    revision.content && typeof revision.content === "object"
      ? revision.content
      : raw.content && typeof raw.content === "object"
        ? raw.content
        : revision;
  const model =
    content.content && typeof content.content === "object" ? content.content : content;
  const design = model.design && typeof model.design === "object" ? model.design : {};
  return { ...design, ...model };
}

export function buildMentalModelCardPayload(model, { source = "build", summaryText } = {}) {
  const moduleList = Array.isArray(model?.modules)
    ? model.modules
        .map((m) => ({
          name: typeof m === "string" ? m : (m.name || ""),
          role: typeof m === "string" ? "module" : (m.role || "module"),
          ports: m.ports || [],
        }))
        .filter((m) => m.name)
    : [];
  const ports = Array.isArray(model?.ports) ? model.ports : [];
  const inferred = {
    "Top module": model?.top_module || "—",
    Modules: moduleList.length || "—",
    Ports: ports.length || "—",
    Parameters: (model?.parameters || []).length || "—",
  };
  const summary =
    summaryText
    || model?.description
    || (model?.top_module
      ? `Top module “${model.top_module}” — derived from your spec and RTL.`
      : "Here's how I understand the design before we plan tests.");
  return {
    summary,
    modules: moduleList.length
      ? moduleList
      : ports.length
        ? [{ name: model?.top_module || "top", ports }]
        : [],
    inferred,
    _source: source,
  };
}

export function makeMentalModelBuiltSaid(model) {
  const top = model?.top_module;
  const nMods = Array.isArray(model?.modules) ? model.modules.length : 0;
  const lead = top
    ? `Mental model built — top module “${top}”`
    : "Mental model built from your spec and RTL";
  const detail = nMods > 0 ? ` (${nMods} module${nMods === 1 ? "" : "s"} indexed).` : ".";
  return makeItem(THREAD_KINDS.SAID, {
    text: `${lead}${detail} Review it below, or open the full graph.`,
  });
}

export function makeMentalModelShowAsk() {
  return makeItem(THREAD_KINDS.ASK, {
    _gate: "mental-model-ready",
    question: "Mental model is ready",
    sub: "Open the graph to explore modules and ports, or confirm when this matches your intent.",
    choices: [
      { id: "show-mental-model", label: "Show mental model" },
      { id: "gate-mm-confirm", label: "Looks right — continue" },
    ],
  });
}

export function makeMentalModelCardItem(model, options) {
  return makeItem(THREAD_KINDS.MENTAL_MODEL, buildMentalModelCardPayload(model, options));
}
