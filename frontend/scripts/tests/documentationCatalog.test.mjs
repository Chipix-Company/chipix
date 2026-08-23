import test from "node:test";
import assert from "node:assert/strict";
import {
  ALLOWED_DOC_NAMES,
  DOC_SECTIONS,
  defaultDocName,
  groupDocsForSidebar,
  isCatalogDocName,
  isUserFacingDoc,
} from "../../src/components/thread-first/documentationCatalog.js";

test("isUserFacingDoc allows only catalog guides", () => {
  assert.equal(isUserFacingDoc({ name: "getting-started.md" }), true);
  assert.equal(isUserFacingDoc({ name: "README.md" }), false);
  assert.equal(isUserFacingDoc({ name: "internal-architecture.md" }), false);
  assert.equal(isUserFacingDoc({ name: "gap_report.md" }), false);
});

test("isCatalogDocName matches allowlist", () => {
  assert.equal(isCatalogDocName("task-board.md"), true);
  assert.equal(isCatalogDocName("readme.md"), false);
});

test("groupDocsForSidebar never adds extras outside catalog", () => {
  const docs = [
    { name: "getting-started.md", title: "Getting started" },
    { name: "README.md", title: "Internal readme" },
    { name: "secret-internal.md", title: "Secret" },
    { name: "glossary.md", title: "Glossary" },
  ];
  const sections = groupDocsForSidebar(docs);
  const names = sections.flatMap((s) => s.items.map((d) => d.name));
  assert.deepEqual(names, ["getting-started.md", "glossary.md"]);
  assert.equal(sections.some((s) => s.label === "More"), false);
});

test("defaultDocName prefers getting-started then catalog order", () => {
  assert.equal(
    defaultDocName([{ name: "glossary.md" }, { name: "getting-started.md" }]),
    "getting-started.md",
  );
  assert.equal(defaultDocName([{ name: "glossary.md" }]), "glossary.md");
});

test("catalog covers all allowed doc names", () => {
  const fromSections = new Set(DOC_SECTIONS.flatMap((s) => s.items.map((i) => i.name)));
  assert.deepEqual(fromSections, ALLOWED_DOC_NAMES);
  assert.equal(ALLOWED_DOC_NAMES.size, 12);
});
