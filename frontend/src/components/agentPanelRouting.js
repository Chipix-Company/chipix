function normalizeCommand(text) {
  return String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

export function isVerificationRunCommand(text) {
  const command = normalizeCommand(text);
  return (
    /\b(run|start|launch|execute)\s+verif/.test(command)
    || /\bstrt\s+verif/.test(command)
    || /\blaunch\s+simulation\b/.test(command)
  );
}

export function shouldStartVerificationFlow(text, currentMode = "") {
  const command = normalizeCommand(text);
  const mode = normalizeCommand(currentMode);
  if (mode === "verification") {
    return /\bverification\s+(workflow|setup|mode)\b/.test(command);
  }
  if (isVerificationRunCommand(command) && !/\b(workflow|setup|mode)\b/.test(command)) {
    return false;
  }
  return /\bverification\s+(workflow|setup|mode)\b/.test(command)
    || /\b(run|start)\s+verification\s+(workflow|setup|mode)\b/.test(command);
}
